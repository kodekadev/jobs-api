"""
EmpleaXChile — Postulaciones automáticas con Playwright.

Flujo:
  1. Login con cookies guardadas en BQ (o login fresco con email/password)
  2. Busca empleos en /trabajo/trabajos-en-chile?Search[keyword]=<cargo>
  3. Por cada empleo: navega, click "Postular ahora", confirma
  4. Guarda cada postulación en BigQuery EMPLEOS

URL base de ofertas: https://empleaxchile.cl/trabajo/trabajos-en-chile
URL individual:      https://empleaxchile.cl/jobs/{id}
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
    _make_pw_context,
    _new_stealth_page,
    _standard_answer,
    _llm_answer_questions,
    _extract_cv_text,
    _save_answers_to_cache,
    job_aplica_al_usuario,
)

BASE_URL  = "https://empleaxchile.cl"
PORTAL_ID = "empleaxchile"

_OFFSITE_COUNTRY_KEYWORDS = {
    "argentina", "perú", "peru", "colombia", "mexico", "méxico",
    "eeuu", "estados unidos", "exterior", "internacional",
}


def _norm(texto: str) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(ch for ch in t if not unicodedata.combining(ch))


# ── Login ─────────────────────────────────────────────────────────────────────

def _get_exc_session(user_id: str, email: str, password: str):
    """
    Retorna una page Playwright autenticada en EmpleaXChile.
    Intenta cookies guardadas primero; si fallan, hace login fresco.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(f"[exc] playwright no instalado")
        return None, None, None

    pw_obj = sync_playwright().start()
    _, browser, ctx, _ = _make_pw_context(pw_obj)

    # Restaurar cookies si existen
    cookies = bq.get_portal_cookies(user_id, PORTAL_ID)
    if cookies:
        try:
            ctx.add_cookies(cookies)
        except Exception:
            pass

    page = _new_stealth_page(ctx)

    # Verificar sesión navegando a página que requiere login
    try:
        page.goto(f"{BASE_URL}/account/profile", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
        if "login" not in page.url and "external" not in page.url:
            print(f"[exc] Sesión restaurada desde cookies para {user_id}")
            return page, browser, ctx
    except Exception:
        pass

    # Login fresco
    print(f"[exc] Haciendo login fresco para {user_id} ({email})")
    try:
        page.goto(f"{BASE_URL}/external/login", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)

        # Llenar email
        for sel in [
            "input[type='email']:visible",
            "input[name='Email']:visible",
            "input[name='email']:visible",
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.fill(email)
                    break
            except Exception:
                pass

        # Click continuar (paso 1)
        for sel in [
            "button:has-text('Continuar'):visible",
            "button[type='submit']:visible",
        ]:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0:
                    btn.click()
                    page.wait_for_timeout(2000)
                    break
            except Exception:
                pass

        # Llenar password (paso 2)
        for sel in [
            "input[type='password']:visible",
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.fill(password)
                    break
            except Exception:
                pass

        # Submit login
        for sel in [
            "button[type='submit']:visible",
            "button:has-text('Iniciar'):visible",
            "button:has-text('Entrar'):visible",
        ]:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0:
                    btn.click()
                    page.wait_for_timeout(5000)
                    break
            except Exception:
                pass

        if "login" in page.url or "external" in page.url:
            print(f"[exc] ! Login falló para {user_id}. URL: {page.url}")
            browser.close()
            pw_obj.stop()
            return None, None, None

        print(f"[exc] Login OK para {user_id}: {page.url}")
        bq.save_portal_cookies(user_id, PORTAL_ID, ctx.cookies(), email=email, password=password)
        return page, browser, ctx

    except Exception as e:
        print(f"[exc] Error login: {e}")
        browser.close()
        pw_obj.stop()
        return None, None, None


# ── Responder preguntas ───────────────────────────────────────────────────────

_GET_LABEL_JS = """(el) => {
    if (el.id) {
        var lbl = document.querySelector('label[for="' + el.id + '"]');
        if (lbl && lbl.innerText && lbl.innerText.trim().length > 3) return lbl.innerText.trim();
    }
    var node = el.parentElement;
    for (var i = 0; i < 8; i++) {
        if (!node) break;
        for (var j = 0; j < node.childNodes.length; j++) {
            var c = node.childNodes[j];
            var tag = (c.tagName || '').toUpperCase();
            if (['P','LABEL','SPAN','H3','H4','STRONG','B','LI'].indexOf(tag) >= 0) {
                var txt = (c.innerText || c.textContent || '').trim();
                if (txt.length > 5 && !c.contains(el)) return txt;
            }
        }
        node = node.parentElement;
    }
    var ph = el.getAttribute('placeholder');
    if (ph && ph.trim().length > 3) return ph.trim();
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

_NUMERIC_KWS = {"PRETENSION", "SUELDO", "RENTA", "SALARIO", "AÑOS DE EXP", "ANOS DE EXP", "EXPERIENCIA"}


def _responder_preguntas(page, user: dict, job_title: str = "") -> None:
    """Responde formulario de preguntas de postulación."""
    _EXCL = {"hidden", "radio", "checkbox", "submit", "button", "file"}

    def _is_numeric(it: dict) -> bool:
        lbl = _norm((it.get("label") or "") + " " + (it.get("placeholder") or ""))
        return it.get("type") == "number" or any(k.lower() in lbl for k in _NUMERIC_KWS)

    # Radios
    radios = page.locator("input[type='radio']:visible").all()
    grupos: dict = {}
    for r in radios:
        try:
            name = r.get_attribute("name") or str(id(r))
            grupos.setdefault(name, []).append(r)
        except Exception:
            pass
    for name, grupo in grupos.items():
        try:
            elegido = grupo[0]
            for r in grupo:
                try:
                    lbl = _norm(r.evaluate(_GET_LABEL_JS) or r.get_attribute("value") or "")
                    if any(k in lbl for k in ("si", "yes", "true")):
                        elegido = r
                        break
                except Exception:
                    pass
            elegido.evaluate("(el) => { el.checked=true; el.dispatchEvent(new Event('change',{bubbles:true})); }")
        except Exception:
            pass

    # Inputs y textareas vacíos
    pending = []
    for inp in page.locator("input:visible").all():
        try:
            t = (inp.get_attribute("type") or "text").lower()
            if not inp.is_disabled() and t not in _EXCL and not (inp.input_value() or "").strip():
                pending.append({
                    "el": inp, "kind": "input",
                    "label": inp.evaluate(_GET_LABEL_JS),
                    "type": t,
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
                    "type": "textarea",
                    "placeholder": ta.get_attribute("placeholder") or "",
                })
        except Exception:
            pass

    answers: dict = {}
    for idx, item in enumerate(pending):
        resp = _standard_answer(item, user)
        if resp is not None:
            answers[idx] = (resp, "perfil")

    sin_resp = [item for idx, item in enumerate(pending) if idx not in answers]
    if sin_resp:
        cv_url  = user.get("cv_url") or user.get("CV_URL") or ""
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
            return re.sub(r"[^\d]", "", str(user.get("pretension_general") or user.get("PRETENSION_GENERAL") or "")) \
                   or str(user.get("experiencia") or "5")
        if it.get("type") == "textarea":
            rv = str(user.get("resumen") or user.get("RESUMEN") or "")
            return rv[:400] if rv else f"Profesional con {user.get('experiencia','5')} años de experiencia."
        return ""

    for idx, item in enumerate(pending):
        try:
            el = item["el"]
            resp, source = answers.get(idx, (_fallback(item), "fallback"))
            if _is_numeric(item):
                resp = re.sub(r"[^\d]", "", resp) or resp
            print(f"    [exc/preg/{source}] '{(item['label'] or item['type'])[:35]}' -> '{resp[:35]}'")
            el.evaluate(_SET_VALUE_JS, resp)
        except Exception:
            pass

    print(f"    [exc] Preguntas: {len(grupos)} radios, {len(pending)} inputs")


# ── Postulación individual ────────────────────────────────────────────────────

def _postular_empleo(page, job_url: str, user: dict, titulo: str) -> "dict | bool":
    """
    Navega al empleo y postula. Retorna dict con ok/descripcion o False.
    """
    try:
        page.goto(job_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)

        # Redirigido a login
        if "login" in page.url or "external" in page.url:
            print(f"    [exc] Sesión expirada en {job_url[:60]}")
            return False

        content = page.content().lower()

        # Ya postulado
        if any(s in content for s in [
            "ya postulaste", "ya te has postulado", "ya has postulado",
            "postulación enviada", "ya aplicaste",
        ]):
            print(f"    [exc] Ya postulado — skip")
            return {"ok": True, "ya_postulado": True, "descripcion": ""}

        # Extraer empresa para filtrar
        empresa = ""
        try:
            empresa = page.evaluate("""() => {
                const links = document.querySelectorAll('.company a, [class*="company"] a, [class*="empresa"] a');
                for (const a of links) {
                    const t = (a.innerText || '').trim();
                    if (t.length > 2) return t;
                }
                const h2 = document.querySelector('h2.company-name, .employer-name');
                return h2 ? h2.innerText.trim() : '';
            }""") or ""
        except Exception:
            pass

        if empresa:
            aplica, motivo = job_aplica_al_usuario(titulo, empresa, user)
            if not aplica:
                print(f"    [exc] SALTADO ({motivo}): empresa '{empresa}'")
                return False

        # Extraer descripción
        descripcion = ""
        try:
            descripcion = page.evaluate("""() => {
                const sels = [
                    '[class*="descripcion"]', '[class*="description"]', '.job-detail',
                    '#descripcion', '.oferta-descripcion', 'article',
                ];
                for (const s of sels) {
                    const el = document.querySelector(s);
                    if (el) {
                        const t = (el.innerText || '').trim();
                        if (t.length > 50) return t.slice(0, 5000);
                    }
                }
                return '';
            }""") or ""
        except Exception:
            pass

        # Click "Postular ahora" / "Postular"
        postular_sels = [
            "a:has-text('Postular ahora'):visible",
            "button:has-text('Postular ahora'):visible",
            "a:has-text('Postular'):visible",
            "button:has-text('Postular'):visible",
            "a[href*='postular']:visible",
            "a[href='#apply']:visible",
        ]
        clicked = False
        for sel in postular_sels:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    txt = ""
                    try:
                        txt = loc.inner_text()[:40]
                    except Exception:
                        pass
                    loc.click()
                    print(f"    [exc] Click postular: '{txt}'")
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            print(f"    [exc] Sin botón postular en {job_url[:60]}")
            return False

        try:
            page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(2500)

        # Si el click redirigió a login, sesión expirada
        if "login" in page.url or "external" in page.url:
            print(f"    [exc] Sesión expirada tras click")
            return False

        content_after = page.content().lower()

        # Postulación directa sin form adicional
        if any(s in content_after for s in [
            "postulación enviada", "postulacion enviada", "gracias por postular",
            "te has postulado", "exitosamente", "hemos recibido",
        ]):
            print(f"    [exc] Postulado directamente")
            return {"ok": True, "descripcion": descripcion, "empresa": empresa}

        # Modal/form de preguntas adicionales
        form_loc = page.locator(
            "form:has([type='submit']), form:has(button[type='submit']), .modal:visible form"
        )
        if form_loc.count() > 0:
            try:
                if form_loc.first.is_visible():
                    _responder_preguntas(page, user, job_title=titulo)
                    page.wait_for_timeout(1500)
            except Exception:
                pass

        # Submit
        submit_sels = [
            "button[type='submit']:visible",
            "input[type='submit']:visible",
            "button:has-text('Enviar'):visible",
            "button:has-text('Postular'):visible",
            "button:has-text('Confirmar'):visible",
        ]
        enviado = ""
        for sel in submit_sels:
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible():
                    try:
                        enviado = el.inner_text()[:30]
                    except Exception:
                        enviado = "submit"
                    try:
                        el.click(timeout=4000)
                    except Exception:
                        try:
                            el.evaluate("el => el.click()")
                        except Exception:
                            pass
                    print(f"    [exc] Submit: '{enviado}'")
                    break
            except Exception:
                continue

        if not enviado:
            print(f"    [exc] Sin botón submit — URL: {page.url[:80]}")
            return False

        page.wait_for_timeout(4000)
        content = page.content().lower()
        confirmed = any(s in content for s in [
            "postulación enviada", "gracias por postular", "exitosamente", "hemos recibido",
        ])
        error = any(s in content for s in [
            "ha ocurrido un error", "error al postular", "inténtalo de nuevo",
        ])
        ok = confirmed or not error
        print(f"    [exc] {'OK' if confirmed else ('Error' if error else 'Sin confirmación clara')}")
        if not ok:
            return False
        return {"ok": True, "descripcion": descripcion, "empresa": empresa}

    except Exception as e:
        print(f"    [exc] Error postular: {e}")
        return False


# ── Función principal ─────────────────────────────────────────────────────────

def postular_empleos_exc(user_id: str, user: dict, max_count: int = 999) -> int:
    """
    Busca y postula empleos en EmpleaXChile para el usuario.

    Args:
        user_id  : ID del usuario en BigQuery
        user     : dict con datos del perfil (cargos, ubicaciones, cv_url, etc.)
        max_count: máximo de postulaciones a enviar

    Returns:
        Número de postulaciones guardadas.
    """
    cuenta = bq.get_portal_account(user_id, PORTAL_ID)
    if not cuenta:
        print(f"[exc] Sin cuenta para {user_id} — ejecuta crear_cuenta primero")
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

    try:
        from playwright.sync_api import sync_playwright
        pw_obj = sync_playwright().start()
    except ImportError:
        print("[exc] playwright no instalado")
        return 0

    _, browser, ctx, _ = _make_pw_context(pw_obj)

    # Restaurar cookies
    cookies = bq.get_portal_cookies(user_id, PORTAL_ID)
    if cookies:
        try:
            ctx.add_cookies(cookies)
        except Exception:
            pass

    page = _new_stealth_page(ctx)
    count = 0
    modo_revision = False
    pending_jobs: list = []

    def _ensure_logged_in() -> bool:
        """Verifica sesión y hace login si es necesario."""
        try:
            page.goto(f"{BASE_URL}/account/profile", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)
            if "login" not in page.url and "external" not in page.url:
                return True
        except Exception:
            pass

        # Login fresco
        try:
            page.goto(f"{BASE_URL}/external/login", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)

            for sel in ["input[type='email']:visible", "input[name='Email']:visible"]:
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0:
                        loc.fill(email)
                        break
                except Exception:
                    pass

            for sel in ["button:has-text('Continuar'):visible", "button[type='submit']:visible"]:
                try:
                    btn = page.locator(sel).first
                    if btn.count() > 0:
                        btn.click()
                        page.wait_for_timeout(2000)
                        break
                except Exception:
                    pass

            for sel in ["input[type='password']:visible"]:
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0:
                        loc.fill(password)
                        break
                except Exception:
                    pass

            for sel in ["button[type='submit']:visible", "button:has-text('Iniciar'):visible"]:
                try:
                    btn = page.locator(sel).first
                    if btn.count() > 0:
                        btn.click()
                        page.wait_for_timeout(5000)
                        break
                except Exception:
                    pass

            if "login" not in page.url and "external" not in page.url:
                bq.save_portal_cookies(user_id, PORTAL_ID, ctx.cookies(), email=email, password=password)
                return True

            return False
        except Exception as e:
            print(f"[exc] Error login: {e}")
            return False

    try:
        if not _ensure_logged_in():
            print(f"[exc] Sin sesión para {user_id} — abortando")
            return 0

        _ident = user.get("EMAIL") or user.get("NOMBRE") or user_id
        print(f"[exc] Iniciando postulaciones para {_ident}")
        applied_ids = bq.get_applied_job_ids(user_id)
        modo_revision = bq.get_modo_revision(user_id)
        print(f"[exc] Modo: {'REVISIÓN (guardará para aprobar)' if modo_revision else 'AUTOPILOT (postula directo)'}")
        pending_urls: set = bq.get_pending_job_urls(user_id, PORTAL_ID) if modo_revision else set()
        if pending_urls:
            print(f"[exc] {len(pending_urls)} empleos ya en cola de revision — se saltaran")

        # Ciudades del usuario normalizadas
        user_cities_norm = set()
        for ub in ubicaciones:
            user_cities_norm.add(_norm(ub.split(",")[0].strip()))

        tipo_busqueda  = (user.get("TIPO_BUSQUEDA") or "").upper()
        _tipo_map      = {"PRACTICA": "1", "TRABAJO": "0", "ESTUDIANTE": "2"}
        job_offer_type = _tipo_map.get(tipo_busqueda, "")

        for cargo in cargos:
            cargo_enc = cargo.replace(" ", "+")
            print(f"[exc] Buscando: '{cargo}'")

            for page_num in range(1, 6):  # Hasta 5 páginas por cargo
                search_url = (
                    f"{BASE_URL}/trabajo/trabajos-en-chile"
                    f"?Search%5Bterms%5D={cargo_enc}"
                    + (f"&Search%5BjobOfferType%5D={job_offer_type}" if job_offer_type else "")
                    + f"&page={page_num}"
                )
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2500)
                except Exception:
                    break

                if "login" in page.url:
                    if not _ensure_logged_in():
                        break
                    page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2500)

                # Recopilar links de empleos (/jobs/{ID} o /jobs/{ID}/{step})
                jobs: list = []
                seen_ids: set = set()
                for a in page.locator("a[href*='/jobs/']").all():
                    try:
                        href  = a.get_attribute("href") or ""
                        title = a.inner_text().strip()
                        m     = re.search(r"/jobs/(\d+)", href)
                        if not m or not title or len(title) <= 5:
                            continue
                        job_id = m.group(1)
                        if job_id in seen_ids:
                            continue
                        full = f"{BASE_URL}/jobs/{job_id}/1"
                        jobs.append({"titulo": title, "link": full, "id": job_id})
                        seen_ids.add(job_id)
                    except Exception:
                        continue

                if not jobs:
                    print(f"[exc] Sin resultados en página {page_num} — deteniendo")
                    break

                print(f"[exc] Página {page_num}: {len(jobs)} empleos encontrados")

                for j, job in enumerate(jobs[:15]):
                    job_id = job["id"]
                    if job_id in applied_ids:
                        print(f"[exc] {j+1}/{len(jobs)} Ya aplicado — skip")
                        continue

                    titulo = job["titulo"]
                    titulo_norm = _norm(titulo)

                    # Filtrar empleos de fuera de Chile
                    if any(kw in titulo_norm for kw in _OFFSITE_COUNTRY_KEYWORDS):
                        print(f"[exc] {j+1}/{len(jobs)} SALTADO (país extranjero): '{titulo[:40]}'")
                        continue

                    aplica, motivo = job_aplica_al_usuario(titulo, "", user)
                    if not aplica:
                        print(f"[exc] {j+1}/{len(jobs)} SALTADO ({motivo}): '{titulo[:40]}'")
                        continue

                    if modo_revision:
                        if job["link"] in pending_urls:
                            print(f"[exc] {j+1}/{len(jobs)} Ya en cola — skip: '{titulo[:40]}'")
                            continue
                        pending_jobs.append({"titulo": titulo, "link": job["link"], "empresa": ""})
                        print(f"[exc] {j+1}/{len(jobs)} PENDIENTE revision: '{titulo[:40]}'")
                        continue

                    print(f"[exc] {j+1}/{len(jobs)} {titulo[:50]}")

                    ok = False
                    try:
                        ok = _postular_empleo(page, job["link"], user, titulo)
                    except Exception as e:
                        if "TargetClosedError" in type(e).__name__ or "closed" in str(e).lower():
                            print(f"[exc] Browser cerrado — reconectando...")
                            if _ensure_logged_in():
                                try:
                                    ok = _postular_empleo(page, job["link"], user, titulo)
                                except Exception:
                                    pass
                        else:
                            print(f"[exc] Error: {e}")

                    if ok and isinstance(ok, dict) and ok.get("ya_postulado"):
                        applied_ids.add(job_id)
                    elif ok:
                        descripcion = (isinstance(ok, dict) and ok.get("descripcion")) or ""
                        empresa_exc = (isinstance(ok, dict) and ok.get("empresa")) or ""

                        bq.save_jobs([{
                            "id_empleo":         job_id,
                            "id_usuario":        user_id,
                            "titulo_empleo":     titulo,
                            "cargo":             cargo,
                            "Fecha_Postulacion": datetime.datetime.utcnow().isoformat(),
                            "empresa":           empresa_exc,
                            "descripcion":       descripcion,
                            "link":              job["link"],
                            "portal":            PORTAL_ID,
                        }])
                        applied_ids.add(job_id)
                        count += 1
                        print(f"[exc] Guardado ({count})")
                        if count >= max_count:
                            raise StopIteration
                    else:
                        try:
                            page.go_back()
                            page.wait_for_timeout(1500)
                        except Exception:
                            pass

                    time.sleep(1.0)

                if count >= max_count:
                    raise StopIteration

    except StopIteration:
        pass
    except Exception as e:
        import traceback
        print(f"[exc] Error general: {e}")
        traceback.print_exc()
    finally:
        try:
            browser.close()
            pw_obj.stop()
        except Exception:
            pass

    if modo_revision and pending_jobs:
        saved = bq.save_pending_jobs(user_id, PORTAL_ID, pending_jobs)
        print(f"[exc] {saved} empleos guardados para revisión:")
        for pj in pending_jobs:
            print(f"  -> {pj['titulo'][:70]}")

    print(f"[exc] Finalizado {user_id} — {count} postulaciones")
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
        postular_empleos_exc(u["ID_USUARIO"], u)
