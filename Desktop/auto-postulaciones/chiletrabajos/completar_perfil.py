"""
ChileTrabajos — Completar perfil / onboarding.

Llena cada sección del editor de CV en /dashboard/editar-cv:
  - Datos Personales (RUT, celular, ciudad, fecha nacimiento)
  - Educación y experiencia (experiencia, resumen, años exp, nivel)
  - Disponibilidad y preferencias (pretensión, ciudad trabajo, intereses)
  - CV adjunto (archivo PDF)
"""
import os
import sys
import re
import time
import json

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bq
from chiletrabajos.login import make_driver, _do_login

BASE_URL  = "https://www.chiletrabajos.cl"
PORTAL_ID = "chiletrabajos"
EDITAR_CV = f"{BASE_URL}/dashboard/editar-cv"


def _js_set(driver, el, value: str):
    driver.execute_script("""
        var el=arguments[0], v=arguments[1];
        var tag=el.tagName.toUpperCase();
        var proto=tag==='TEXTAREA'?window.HTMLTextAreaElement.prototype:window.HTMLInputElement.prototype;
        var s=Object.getOwnPropertyDescriptor(proto,'value');
        if(s) s.set.call(el,v);
        el.dispatchEvent(new Event('input',{bubbles:true}));
        el.dispatchEvent(new Event('change',{bubbles:true}));
    """, el, value)


def _abrir_seccion(driver: webdriver.Chrome, texto_boton: str) -> "webdriver.WebElement | None":
    """Clickea el botón de sección y retorna el offcanvas abierto."""
    try:
        btn = driver.find_element(
            By.XPATH,
            f"//button[contains(normalize-space(.),'{texto_boton[:20]}')]"
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        time.sleep(0.3)
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(2)

        paneles = driver.find_elements(
            By.XPATH,
            "//*[contains(@class,'offcanvas') and contains(@class,'show')] | "
            "//*[contains(@class,'modal') and contains(@class,'show')]"
        )
        panel = paneles[0] if paneles else None
        print(f"  [cht-perfil] Sección '{texto_boton}': {'abierta' if panel else 'sin panel'}")
        return panel
    except Exception as e:
        print(f"  [cht-perfil] Error abriendo '{texto_boton}': {e}")
        return None


def _guardar_seccion(driver: webdriver.Chrome, panel):
    """Clickea el botón guardar/actualizar dentro del panel."""
    ctx = panel if panel else driver
    for xp in [
        ".//button[@type='submit']",
        ".//input[@type='submit']",
        ".//button[contains(translate(.,'GUARDAR','guardar'),'guardar')]",
        ".//button[contains(translate(.,'ACTUALIZAR','actualizar'),'actualizar')]",
        ".//button[contains(translate(.,'ENVIAR','enviar'),'enviar')]",
    ]:
        try:
            btns = [b for b in ctx.find_elements(By.XPATH, xp) if b.is_displayed()]
            if btns:
                driver.execute_script("arguments[0].click();", btns[0])
                print(f"  [cht-perfil] Guardado: '{(btns[0].text or btns[0].get_attribute('value') or '')[:30]}'")
                time.sleep(3)
                return True
        except Exception:
            pass
    return False


def _cerrar_panel(driver: webdriver.Chrome, panel):
    if not panel:
        return
    try:
        close = panel.find_element(
            By.XPATH,
            ".//button[@data-bs-dismiss='offcanvas'] | "
            ".//button[@data-bs-dismiss='modal'] | "
            ".//button[contains(@class,'btn-close')]"
        )
        driver.execute_script("arguments[0].click();", close)
        time.sleep(1.5)
    except Exception:
        pass


# ── secciones ─────────────────────────────────────────────────────────────────

def _fill_datos_personales(driver: webdriver.Chrome, user: dict):
    panel = _abrir_seccion(driver, "Datos Personales")
    if not panel:
        return

    rut      = str(user.get("RUT") or user.get("rut") or "")
    cel_raw  = str(user.get("CELULAR") or user.get("celular") or "")
    cel      = re.sub(r"\D", "", cel_raw)[-9:] if cel_raw else ""
    fn_raw   = str(user.get("FECHA_NACIMIENTO") or user.get("fecha_nacimiento") or "")
    ciudad   = str((user.get("UBICACIONES") or ["Santiago"])[0] if isinstance(
        user.get("UBICACIONES") or user.get("ubicaciones"), list)
        else str(user.get("UBICACIONES") or user.get("ubicaciones") or "Santiago")).split(",")[0].strip()

    try:
        ubicaciones = user.get("UBICACIONES") or user.get("ubicaciones") or []
        if isinstance(ubicaciones, str):
            ubicaciones = json.loads(ubicaciones)
        ciudad = ubicaciones[0].split(",")[0].strip() if ubicaciones else "Santiago"
    except Exception:
        ciudad = "Santiago"

    def _fill_id(field_id: str, value: str):
        if not value:
            return
        try:
            el = panel.find_element(By.ID, field_id)
            if not (el.get_attribute("value") or "").strip() or field_id in ("ciu", "rut", "cel"):
                _js_set(driver, el, value)
                print(f"  [cht-perfil]   {field_id} = '{value[:30]}'")
        except Exception:
            pass

    _fill_id("rut", rut)
    _fill_id("cel", cel)
    _fill_id("ciu", ciudad)

    # Fecha de nacimiento (selectores day/month/year)
    if fn_raw:
        try:
            parts = fn_raw.split("-")
            ano, mes, dia = parts[0], parts[1].zfill(2), parts[2].zfill(2)
            for field_id, val in [("day", dia), ("month", mes), ("year", ano)]:
                try:
                    sel = Select(panel.find_element(By.ID, field_id))
                    opts = [o.get_attribute("value") for o in sel.options]
                    # Intentar con valor tal cual, sin cero y como entero
                    candidates = [val, val.lstrip("0"), str(int(val))]
                    chosen = next((c for c in candidates if c in opts), None)
                    if chosen:
                        sel.select_by_value(chosen)
                        print(f"  [cht-perfil]   {field_id} = {chosen}")
                    else:
                        print(f"  [cht-perfil]   {field_id} opts={opts[:5]} (no match para {val})")
                except Exception:
                    pass
        except Exception:
            pass

    _guardar_seccion(driver, panel)
    _cerrar_panel(driver, panel)


def _fill_educacion_experiencia(driver: webdriver.Chrome, user: dict):
    panel = _abrir_seccion(driver, "Educación y experiencia")
    if not panel:
        return

    profesion  = str(user.get("PROFESION") or user.get("profesion") or "")
    resumen    = str(user.get("RESUMEN") or user.get("resumen") or "")
    empresa    = str(user.get("EMPRESA") or user.get("empresa") or "")
    exp_raw    = str(user.get("EXPERIENCIA") or user.get("experiencia") or "5")
    exp_num    = re.sub(r"[^\d]", "", exp_raw) or "5"
    nivel      = str(user.get("NIVEL_EDUCATIVO") or user.get("nivel_educativo") or "")

    # id=exp → experiencia laboral (descripción)
    exp_texto = resumen or f"Profesional con {exp_num} años de experiencia en {profesion}."
    try:
        el = panel.find_element(By.ID, "exp")
        if not (el.get_attribute("value") or "").strip():
            _js_set(driver, el, exp_texto[:500])
            print(f"  [cht-perfil]   exp = '{exp_texto[:60]}...'")
    except Exception:
        pass

    # id=resumen → resumen educación
    resumen_edu = str(user.get("CARRERA") or user.get("carrera") or profesion)
    try:
        el = panel.find_element(By.ID, "resumen")
        if not (el.get_attribute("value") or "").strip():
            _js_set(driver, el, resumen_edu[:300])
            print(f"  [cht-perfil]   resumen = '{resumen_edu[:60]}'")
    except Exception:
        pass

    # id=anioexp → años de experiencia (select)
    try:
        sel = Select(panel.find_element(By.ID, "anioexp"))
        opts = [o.get_attribute("value") for o in sel.options]
        target = exp_num if exp_num in opts else next(
            (v for v in opts if v and v.isdigit() and int(v) <= int(exp_num)), opts[-1] if opts else "5"
        )
        sel.select_by_value(target)
        print(f"  [cht-perfil]   anioexp = {target}")
    except Exception:
        pass

    # id=nivel → nivel educacional (select)
    if nivel:
        try:
            sel = Select(panel.find_element(By.ID, "nivel"))
            opts_txt = [o.text.strip().upper() for o in sel.options]
            opts_val = [o.get_attribute("value") for o in sel.options]
            match = next(
                (opts_val[i] for i, t in enumerate(opts_txt)
                 if nivel.upper() in t or t in nivel.upper()), None
            )
            if match:
                sel.select_by_value(match)
                print(f"  [cht-perfil]   nivel = {match}")
        except Exception:
            pass

    _guardar_seccion(driver, panel)
    _cerrar_panel(driver, panel)


def _fill_disponibilidad(driver: webdriver.Chrome, user: dict):
    panel = _abrir_seccion(driver, "Disponibilidad y preferencias")
    if not panel:
        return

    pret_raw = str(user.get("PRETENSION_GENERAL") or user.get("pretension_general") or "")
    pret_num = re.sub(r"[^\d]", "", pret_raw) or ""

    try:
        el = panel.find_element(By.ID, "pretensiones")
        if not (el.get_attribute("value") or "").strip() and pret_num:
            _js_set(driver, el, pret_num)
            print(f"  [cht-perfil]   pretensiones = {pret_num}")
    except Exception:
        pass

    # Ciudad donde trabajar
    try:
        ubicaciones = user.get("UBICACIONES") or user.get("ubicaciones") or []
        if isinstance(ubicaciones, str):
            ubicaciones = json.loads(ubicaciones)
        ciudad = ubicaciones[0].split(",")[0].strip() if ubicaciones else "Santiago"
        el = panel.find_element(By.ID, "trabajociu")
        if not (el.get_attribute("value") or "").strip():
            _js_set(driver, el, ciudad)
            print(f"  [cht-perfil]   trabajociu = {ciudad}")
    except Exception:
        pass

    # Intereses (cargos)
    try:
        cargos = user.get("CARGOS") or user.get("cargos") or []
        if isinstance(cargos, str):
            cargos = json.loads(cargos)
        intereses = ", ".join(cargos[:3]) if cargos else ""
        if intereses:
            el = panel.find_element(By.ID, "intereses")
            if not (el.get_attribute("value") or "").strip():
                _js_set(driver, el, intereses[:200])
                print(f"  [cht-perfil]   intereses = '{intereses[:60]}'")
    except Exception:
        pass

    _guardar_seccion(driver, panel)
    _cerrar_panel(driver, panel)


def _upload_cv(driver: webdriver.Chrome, user: dict):
    """Sube el PDF del CV al portal."""
    import tempfile, requests
    cv_url = str(user.get("CV_URL") or user.get("cv_url") or "")
    if not cv_url:
        print("  [cht-perfil]   Sin cv_url — saltando")
        return

    panel = _abrir_seccion(driver, "Agregar / Editar CV")
    if not panel:
        return

    try:
        tmp_dir  = tempfile.mkdtemp()
        tmp_path = os.path.join(tmp_dir, "CV.pdf")

        if "storage.googleapis.com" in cv_url or cv_url.startswith("gs://"):
            from google.cloud import storage as gcs
            parts = cv_url.replace("https://storage.googleapis.com/", "").split("/", 1)
            gcs.Client().bucket(parts[0]).blob(parts[1]).download_to_filename(tmp_path)
        else:
            r = requests.get(cv_url, timeout=30)
            r.raise_for_status()
            with open(tmp_path, "wb") as f:
                f.write(r.content)

        print(f"  [cht-perfil]   CV descargado ({os.path.getsize(tmp_path)//1024} KB)")

        # Buscar file input dentro del panel (o en toda la página si el panel es pequeño)
        file_inps = panel.find_elements(By.CSS_SELECTOR, "input[type='file']")
        if not file_inps:
            file_inps = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
        if not file_inps:
            print("  [cht-perfil]   Sin input file en panel")
            return
        file_inp = file_inps[0]
        driver.execute_script(
            "arguments[0].style.display='block';arguments[0].style.visibility='visible';",
            file_inp
        )
        file_inp.send_keys(tmp_path)
        time.sleep(3)
        print("  [cht-perfil]   CV enviado al input")
        _guardar_seccion(driver, panel)

    except Exception as e:
        print(f"  [cht-perfil]   CV upload error: {e}")

    _cerrar_panel(driver, panel)


# ── función principal ─────────────────────────────────────────────────────────

def _selenium_completar_perfil_chiletrabajos(user_id: str, user: dict) -> bool:
    """
    Completa el perfil de ChileTrabajos usando los IDs de campo reales
    descubiertos en /dashboard/editar-cv.
    """
    cuenta = bq.get_portal_account(user_id, PORTAL_ID)
    if not cuenta:
        print(f"  [cht-perfil] Sin cuenta para {user_id}")
        return False

    driver = None
    try:
        driver = make_driver()
        if not _do_login(driver, cuenta["email"], cuenta["password"]):
            driver.quit()
            return False

        driver.get(EDITAR_CV)
        time.sleep(5)
        print(f"  [cht-perfil] En editar-cv: {driver.current_url}")

        _fill_datos_personales(driver, user)
        _fill_educacion_experiencia(driver, user)
        _fill_disponibilidad(driver, user)
        _upload_cv(driver, user)

        bq.save_portal_cookies(user_id, PORTAL_ID, driver.get_cookies())
        print(f"  [cht-perfil] Perfil completado para {user_id}")
        driver.quit()
        return True

    except Exception as e:
        import traceback
        print(f"  [cht-perfil] Error: {e}")
        traceback.print_exc()
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        return False


# ── Playwright ────────────────────────────────────────────────────────────────

def _pw_js_set(page, handle, value: str):
    page.evaluate("""
        ([el, v]) => {
            var tag = el.tagName.toUpperCase();
            var proto = tag === 'TEXTAREA'
                ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            var s = Object.getOwnPropertyDescriptor(proto, 'value');
            if (s) s.set.call(el, v); else el.value = v;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
        }
    """, [handle, value])


def _pw_fill_id(page, panel_loc, field_id: str, value: str):
    if not value:
        return False
    try:
        el = panel_loc.locator(f"#{field_id}").first
        el.wait_for(state="visible", timeout=3000)
        cur = el.input_value() if el.evaluate("e => e.tagName") != "SELECT" else ""
        if not cur.strip() or field_id in ("ciu", "rut", "cel"):
            _pw_js_set(page, el.element_handle(), value)
            print(f"  [cht-pw-perfil]   {field_id} = '{value[:30]}'")
            return True
    except Exception:
        pass
    return False


def _pw_select_id(page, panel_loc, field_id: str, value: str, label: str):
    if not value:
        return
    try:
        sel_loc = panel_loc.locator(f"#{field_id}")
        sel_loc.select_option(value=value)
        print(f"  [cht-pw-perfil]   {label} = {value}")
    except Exception:
        pass


def _pw_abrir_seccion(page, texto: str):
    try:
        btn = page.locator(f"button:has-text('{texto[:20]}')").first
        btn.click()
        page.wait_for_timeout(2000)
        panel = page.locator(".offcanvas.show, .modal.show").first
        exists = panel.count() > 0
        print(f"  [cht-pw-perfil] Sección '{texto}': {'abierta' if exists else 'sin panel'}")
        return panel if exists else None
    except Exception as e:
        print(f"  [cht-pw-perfil] Error abriendo '{texto}': {e}")
        return None


def _pw_guardar_seccion(page, panel_loc):
    if panel_loc is None:
        return False
    for sel in [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Guardar')",
        "button:has-text('Actualizar')",
        "button:has-text('Enviar')",
    ]:
        try:
            btn = panel_loc.locator(sel).first
            if btn.count() > 0 and btn.is_visible():
                txt = btn.inner_text()[:30]
                btn.click()
                print(f"  [cht-pw-perfil] Guardado: '{txt}'")
                page.wait_for_timeout(3000)
                return True
        except Exception:
            pass
    return False


def _pw_cerrar_panel(panel_loc):
    if panel_loc is None:
        return
    try:
        close = panel_loc.locator(
            "button[data-bs-dismiss='offcanvas'], button[data-bs-dismiss='modal'], button.btn-close"
        ).first
        if close.count() > 0:
            close.click()
    except Exception:
        pass


def _pw_completar_perfil_chiletrabajos(user_id: str, user: dict) -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False

    cuenta = bq.get_portal_account(user_id, PORTAL_ID)
    if not cuenta:
        return False

    cloud = bool(os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB"))
    headless = cloud  # visible en local para poder ver qué pasa

    rut     = str(user.get("RUT") or user.get("rut") or "")
    cel_raw = str(user.get("CELULAR") or user.get("celular") or "")
    cel     = re.sub(r"\D", "", cel_raw)[-9:] if cel_raw else ""
    fn_raw  = str(user.get("FECHA_NACIMIENTO") or user.get("fecha_nacimiento") or "")
    profesion = str(user.get("PROFESION") or user.get("profesion") or "")
    resumen   = str(user.get("RESUMEN") or user.get("resumen") or "")
    exp_raw   = str(user.get("EXPERIENCIA") or user.get("experiencia") or "5")
    exp_num   = re.sub(r"[^\d]", "", exp_raw) or "5"
    nivel     = str(user.get("NIVEL_EDUCATIVO") or user.get("nivel_educativo") or "")
    pret_raw  = str(user.get("PRETENSION_GENERAL") or user.get("pretension_general") or "")
    pret_num  = re.sub(r"[^\d]", "", pret_raw) or ""
    cv_url    = str(user.get("CV_URL") or user.get("cv_url") or "")

    try:
        ubicaciones = user.get("UBICACIONES") or user.get("ubicaciones") or []
        if isinstance(ubicaciones, str):
            import json as _json
            ubicaciones = _json.loads(ubicaciones)
        ciudad = ubicaciones[0].split(",")[0].strip() if ubicaciones else "Santiago"
    except Exception:
        ciudad = "Santiago"

    try:
        cargos = user.get("CARGOS") or user.get("cargos") or []
        if isinstance(cargos, str):
            import json as _json
            cargos = _json.loads(cargos)
        intereses = ", ".join(cargos[:3]) if cargos else ""
    except Exception:
        intereses = ""

    try:
        with sync_playwright() as pw:
            args = ["--no-sandbox", "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled"]
            if cloud:
                args.append("--single-process")
            browser = pw.chromium.launch(headless=headless, args=args)
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 768},
            )
            page = ctx.new_page()

            # Login — ChileTrabajos form clásico HTML, usar fill() no js setter
            page.goto(f"{BASE_URL}/chtlogin", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            try:
                page.locator("#username").wait_for(state="visible", timeout=10000)
                page.locator("#username").fill(cuenta["email"])
                page.locator("#password").fill(cuenta["password"])
                print(f"  [cht-pw-perfil] Credenciales llenadas")
            except Exception as _e:
                print(f"  [cht-pw-perfil] Error llenando login: {_e}")
                browser.close()
                return False
            # Click submit — XPath cubre input[@value=] y button[@type=submit]
            try:
                page.locator("xpath=//input[@value='Iniciar Sesión'] | //button[@type='submit']").first.click()
            except Exception as _e:
                print(f"  [cht-pw-perfil] Error click submit: {_e}")
            page.wait_for_timeout(5000)
            print(f"  [cht-pw-perfil] URL tras login: {page.url}")
            if "chtlogin" in page.url:
                print(f"  [cht-pw-perfil] Login falló")
                browser.close()
                return False
            print(f"  [cht-pw-perfil] Login OK: {page.url}")

            # Ir a editar-cv
            page.goto(EDITAR_CV, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            print(f"  [cht-pw-perfil] En editar-cv: {page.url}")

            # ── Datos Personales ──────────────────────────────────────────────
            panel = _pw_abrir_seccion(page, "Datos Personales")
            if panel:
                _pw_fill_id(page, panel, "rut", rut)
                _pw_fill_id(page, panel, "cel", cel)
                _pw_fill_id(page, panel, "ciu", ciudad)
                if fn_raw:
                    try:
                        parts = fn_raw.split("-")
                        ano, mes, dia = parts[0], parts[1].zfill(2), parts[2].zfill(2)
                        for fid, val, candidates in [
                            ("day",   dia, [dia, dia.lstrip("0"), str(int(dia))]),
                            ("month", mes, [mes, mes.lstrip("0"), str(int(mes))]),
                            ("year",  ano, [ano]),
                        ]:
                            for c in candidates:
                                try:
                                    panel.locator(f"#{fid}").select_option(value=c)
                                    print(f"  [cht-pw-perfil]   {fid} = {c}")
                                    break
                                except Exception:
                                    pass
                    except Exception:
                        pass
                _pw_guardar_seccion(page, panel)
                _pw_cerrar_panel(panel)

            # ── Educación y experiencia ───────────────────────────────────────
            panel = _pw_abrir_seccion(page, "Educación y experiencia")
            if panel:
                exp_texto = resumen or f"Profesional con {exp_num} años de experiencia en {profesion}."
                _pw_fill_id(page, panel, "exp", exp_texto[:500])
                carrera = str(user.get("CARRERA") or user.get("carrera") or profesion)
                _pw_fill_id(page, panel, "resumen", carrera[:300])
                # anioexp select: best match <= exp_num
                try:
                    opts = panel.locator("#anioexp option").all()
                    vals = [o.get_attribute("value") for o in opts]
                    target = exp_num if exp_num in vals else next(
                        (v for v in reversed(vals) if v and v.isdigit() and int(v) <= int(exp_num)), vals[-1] if vals else "5"
                    )
                    panel.locator("#anioexp").select_option(value=target)
                    print(f"  [cht-pw-perfil]   anioexp = {target}")
                except Exception:
                    pass
                # nivel educacional
                if nivel:
                    try:
                        opts = panel.locator("#nivel option").all()
                        match = next(
                            (o.get_attribute("value") for o in opts
                             if nivel.upper() in (o.inner_text().strip().upper())
                             or o.inner_text().strip().upper() in nivel.upper()), None
                        )
                        if match:
                            panel.locator("#nivel").select_option(value=match)
                            print(f"  [cht-pw-perfil]   nivel = {match}")
                    except Exception:
                        pass
                _pw_guardar_seccion(page, panel)
                _pw_cerrar_panel(panel)

            # ── Disponibilidad ────────────────────────────────────────────────
            panel = _pw_abrir_seccion(page, "Disponibilidad y preferencias")
            if panel:
                if pret_num:
                    _pw_fill_id(page, panel, "pretensiones", pret_num)
                _pw_fill_id(page, panel, "trabajociu", ciudad)
                if intereses:
                    _pw_fill_id(page, panel, "intereses", intereses[:200])
                _pw_guardar_seccion(page, panel)
                _pw_cerrar_panel(panel)

            # ── CV adjunto ────────────────────────────────────────────────────
            if cv_url:
                panel = _pw_abrir_seccion(page, "Agregar / Editar CV")
                if panel:
                    try:
                        import tempfile, requests as _req
                        tmp_dir  = tempfile.mkdtemp()
                        tmp_path = os.path.join(tmp_dir, "CV.pdf")
                        if "storage.googleapis.com" in cv_url or cv_url.startswith("gs://"):
                            from google.cloud import storage as gcs
                            parts = cv_url.replace("https://storage.googleapis.com/", "").split("/", 1)
                            gcs.Client().bucket(parts[0]).blob(parts[1]).download_to_filename(tmp_path)
                        else:
                            r = _req.get(cv_url, timeout=30)
                            r.raise_for_status()
                            with open(tmp_path, "wb") as f:
                                f.write(r.content)
                        print(f"  [cht-pw-perfil]   CV descargado ({os.path.getsize(tmp_path)//1024} KB)")
                        file_loc = panel.locator("input[type='file']").first
                        if file_loc.count() == 0:
                            file_loc = page.locator("input[type='file']").first
                        page.evaluate("el => { el.style.display='block'; el.style.visibility='visible'; }",
                                      file_loc.element_handle())
                        file_loc.set_input_files(tmp_path)
                        page.wait_for_timeout(3000)
                        print(f"  [cht-pw-perfil]   CV enviado al input")
                        _pw_guardar_seccion(page, panel)
                    except Exception as e:
                        print(f"  [cht-pw-perfil]   CV upload error: {e}")
                    _pw_cerrar_panel(panel)

            # Guardar cookies en BQ
            cookies = ctx.cookies()
            bq.save_portal_cookies(user_id, PORTAL_ID, cookies)
            print(f"  [cht-pw-perfil] Perfil completado para {user_id}")
            browser.close()
            return True

    except Exception as e:
        import traceback
        print(f"  [cht-pw-perfil] Error: {e}")
        traceback.print_exc()
        return False


def completar_perfil_chiletrabajos(user_id: str, user: dict) -> bool:
    """Completa el perfil de ChileTrabajos. Playwright primero, Selenium como fallback."""
    try:
        print(f"  [cht-perfil] Intentando Playwright...")
        if _pw_completar_perfil_chiletrabajos(user_id, user):
            return True
        print(f"  [cht-perfil] Playwright sin éxito — fallback a Selenium...")
    except Exception as e:
        print(f"  [cht-perfil] Playwright error: {e} — fallback a Selenium...")
    return _selenium_completar_perfil_chiletrabajos(user_id, user)


if __name__ == "__main__":
    os.environ.setdefault(
        "GOOGLE_APPLICATION_CREDENTIALS",
        r"C:\Users\bastian\Desktop\Script_Python\jobs-425301-ba25295bbbd0.json",
    )
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
    users = bq.get_active_users()
    for u in users:
        completar_perfil_chiletrabajos(u["ID_USUARIO"], u)
