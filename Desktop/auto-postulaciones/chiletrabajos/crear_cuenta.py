"""
ChileTrabajos — Creación de cuenta nueva.

Flujo:
  1. Navega a /chtregister (o /registro)
  2. Llena nombre, apellido, email, password
  3. Guarda credenciales en BigQuery CUENTAS_PORTALES (portal='chiletrabajos')

Uso:
    from chiletrabajos.crear_cuenta import crear_cuenta_chiletrabajos
    ok = crear_cuenta_chiletrabajos(user_id="jobs2", user=perfil_dict)
"""
import os
import sys
import re
import time
import secrets
import string

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bq

BASE_URL  = "https://www.chiletrabajos.cl"
PORTAL_ID = "chiletrabajos"


# ── helpers ───────────────────────────────────────────────────────────────────

_CHROMIUM = "/usr/bin/chromium"
_CHROMEDRIVER = "/usr/bin/chromedriver"

_xvfb_started = False


def _ensure_xvfb():
    global _xvfb_started
    if _xvfb_started:
        return
    try:
        import subprocess
        subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1366x768x24", "-ac"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        os.environ["DISPLAY"] = ":99"
        time.sleep(1.5)
        _xvfb_started = True
        print("  [cht] Xvfb :99 iniciado")
    except Exception as e:
        print(f"  [cht] Xvfb falló: {e}")


def _make_driver() -> webdriver.Chrome:
    from selenium.webdriver.chrome.service import Service

    in_linux = os.path.exists(_CHROMIUM)
    if in_linux:
        _ensure_xvfb()

    options = Options()
    if not in_linux:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1366,768")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    if in_linux:
        options.binary_location = _CHROMIUM
        driver = webdriver.Chrome(service=Service(_CHROMEDRIVER), options=options)
    else:
        driver = webdriver.Chrome(options=options)

    driver.execute_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    return driver


def _generar_clave() -> str:
    chars = string.ascii_letters + string.digits + "!@#$"
    while True:
        pwd = "".join(secrets.choice(chars) for _ in range(12))
        if (any(c.isupper() for c in pwd)
                and any(c.isdigit() for c in pwd)
                and any(c in "!@#$" for c in pwd)):
            return pwd


def _js_set(driver, el, value: str):
    driver.execute_script("""
        var el=arguments[0], v=arguments[1];
        var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');
        if(s) s.set.call(el,v);
        el.dispatchEvent(new Event('input',{bubbles:true}));
        el.dispatchEvent(new Event('change',{bubbles:true}));
    """, el, value)


# ── función principal ─────────────────────────────────────────────────────────

def _selenium_crear_cuenta_chiletrabajos(user_id: str, user: dict) -> bool:
    """
    Crea una cuenta en ChileTrabajos para el usuario dado.
    Guarda email y contraseña en BigQuery CUENTAS_PORTALES.

    Args:
        user_id : ID del usuario en BigQuery (ej: 'jobs2')
        user    : dict con NOMBRE, EMAIL, y opcionalmente celular

    Returns:
        True si la cuenta se creó o ya existía.
    """
    # Si ya tiene cuenta guardada, no crear otra
    cuenta = bq.get_portal_account(user_id, PORTAL_ID)
    if cuenta:
        print(f"  [cht] Cuenta ya existente para {user_id}: {cuenta['email']}")
        return True

    nombre_completo = str(user.get("NOMBRE") or user.get("nombre") or "")
    partes   = nombre_completo.split()
    nombre   = partes[0] if partes else "Usuario"
    apellido = partes[1] if len(partes) > 1 else "Apellido"

    # Mismo patrón que Trabajando.cl: {nombre}.{alo3}{hex6}@gmail.com
    nombre_slug   = re.sub(r"[^a-z0-9]", "", nombre.lower())
    apellido_slug = re.sub(r"[^a-z0-9]", "", apellido.lower())[:3]
    codigo        = secrets.token_hex(3)
    email         = f"{nombre_slug}.{apellido_slug}{codigo}@gmail.com"
    clave         = _generar_clave()

    driver = None
    try:
        driver = _make_driver()
        wait   = WebDriverWait(driver, 20)

        # ── 1. Navegar a la página de registro ────────────────────────────────
        for reg_url in [
            f"{BASE_URL}/chtregister",
            f"{BASE_URL}/registro",
            f"{BASE_URL}/crear-cuenta",
        ]:
            driver.get(reg_url)
            time.sleep(3)
            if "login" not in driver.current_url.lower() and "404" not in driver.title.lower():
                print(f"  [cht] Registro en: {driver.current_url}")
                break

        # ── 2. Llenar formulario ───────────────────────────────────────────────
        def _fill(placeholder_kws: list, value: str, label: str):
            for kw in placeholder_kws:
                inps = [i for i in driver.find_elements(By.TAG_NAME, "input")
                        if i.is_displayed()
                        and kw.lower() in (i.get_attribute("placeholder") or "").lower()
                        + (i.get_attribute("name") or "").lower()
                        + (i.get_attribute("id") or "").lower()]
                if inps:
                    _js_set(driver, inps[0], value)
                    print(f"  [cht] {label}: '{value[:30]}'")
                    return
            # Fallback: primer input vacío visible
            inps = [i for i in driver.find_elements(By.TAG_NAME, "input")
                    if i.is_displayed() and not (i.get_attribute("value") or "").strip()
                    and (i.get_attribute("type") or "text") not in ("hidden","submit","button","checkbox","radio")]
            if inps:
                _js_set(driver, inps[0], value)
                print(f"  [cht] {label} (fallback): '{value[:30]}'")

        _fill(["nombre", "first", "name"],     nombre,   "nombre")
        _fill(["apellido", "last", "surname"],  apellido, "apellido")
        _fill(["email", "correo", "mail", "usuario", "username"], email, "email")

        # Contraseña (puede haber dos campos: password + confirm)
        pwd_fields = [i for i in driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
                      if i.is_displayed()]
        for pf in pwd_fields:
            _js_set(driver, pf, clave)
        if pwd_fields:
            print(f"  [cht] password: {len(pwd_fields)} campo(s) llenados")

        time.sleep(0.8)

        # ── 3. Aceptar términos si hay checkbox ────────────────────────────────
        for chk in driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']"):
            try:
                if chk.is_displayed() and not chk.is_selected():
                    driver.execute_script("arguments[0].click();", chk)
            except Exception:
                pass

        # ── 4. Submit ──────────────────────────────────────────────────────────
        for xp in [
            "//button[@type='submit']",
            "//input[@type='submit']",
            "//button[contains(translate(.,'REGISTRARSE','registrarse'),'registrar')]",
            "//button[contains(translate(.,'CREAR CUENTA','crear cuenta'),'crear')]",
            "//button",
        ]:
            btns = [b for b in driver.find_elements(By.XPATH, xp) if b.is_displayed()]
            if btns:
                try:
                    btn_txt = btns[0].text.strip()[:40] or btns[0].get_attribute("value") or ""
                except Exception:
                    btn_txt = ""
                driver.execute_script("arguments[0].click();", btns[0])
                print(f"  [cht] Submit: '{btn_txt}'")
                break

        time.sleep(6)

        # ── 5. Verificar éxito ─────────────────────────────────────────────────
        content = driver.page_source.lower()
        success_signals = [
            "cuenta creada", "registro exitoso", "bienvenido", "welcome",
            "tu cuenta", "verificar", "confirmar", "dashboard", "mi perfil",
            email.lower(),
        ]
        error_signals = [
            "ya existe", "already registered", "ya está registrado",
            "correo en uso", "email already",
        ]

        if any(s in content for s in error_signals):
            print(f"  [cht] Email ya registrado — guardando credencial existente")
            bq.save_portal_account(user_id, PORTAL_ID, email, clave)
            driver.quit()
            return True

        if any(s in content for s in success_signals) or "chtlogin" not in driver.current_url:
            print(f"  [cht] Cuenta creada OK para {user_id} ({email})")
            bq.save_portal_account(user_id, PORTAL_ID, email, clave)
            driver.quit()
            return True

        print(f"  [cht] ! No se pudo confirmar creación. URL: {driver.current_url}")
        driver.quit()
        return False

    except Exception as e:
        import traceback
        print(f"  [cht] Error crear_cuenta: {e}")
        traceback.print_exc()
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        return False


# ── Playwright ────────────────────────────────────────────────────────────────

def _cht_in_cloud_run() -> bool:
    return bool(os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB"))


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


def _pw_fill(page, selector: str, value: str, label: str) -> bool:
    try:
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=3000)
        handle = loc.element_handle()
        _pw_js_set(page, handle, value)
        print(f"  [cht-pw] {label}: '{value[:40]}'")
        return True
    except Exception:
        return False


def _pw_abrir_sec(page, texto: str):
    """Abre offcanvas de sección y retorna locator del panel."""
    try:
        page.locator(f"button:has-text('{texto[:20]}')").first.click()
        page.wait_for_timeout(2000)
        panel = page.locator(".offcanvas.show, .modal.show").first
        ok = panel.count() > 0
        print(f"  [cht-pw] Sección '{texto}': {'abierta' if ok else 'sin panel'}")
        return panel if ok else None
    except Exception as e:
        print(f"  [cht-pw] Error abriendo '{texto}': {e}")
        return None


def _pw_guardar_sec(page, panel):
    if panel is None:
        return
    for sel in ["button[type='submit']", "button:has-text('Guardar')", "button:has-text('Actualizar')"]:
        try:
            btn = panel.locator(sel).first
            if btn.count() > 0 and btn.is_visible():
                print(f"  [cht-pw] Guardado: '{btn.inner_text()[:25]}'")
                btn.click()
                page.wait_for_timeout(3000)
                return
        except Exception:
            pass


def _pw_fill_sec(page, panel, field_id: str, value: str):
    if not value or panel is None:
        return
    try:
        el = panel.locator(f"#{field_id}").first
        el.wait_for(state="visible", timeout=3000)
        el.fill(value)
        print(f"  [cht-pw]   {field_id} = '{value[:30]}'")
    except Exception:
        pass


def _pw_select_sec(panel, field_id: str, value: str, candidates: list | None = None):
    if not value or panel is None:
        return
    for v in ([value] + (candidates or [])):
        try:
            panel.locator(f"#{field_id}").select_option(value=v)
            print(f"  [cht-pw]   {field_id} = {v}")
            return
        except Exception:
            pass


def _pw_perfil_misma_sesion(page, user: dict, user_id: str, email: str = "", clave: str = ""):
    """Completa secciones del perfil de ChileTrabajos en la sesión activa."""
    import json as _json, re as _re

    EDITAR_CV = f"{BASE_URL}/dashboard/editar-cv"

    # ChileTrabajos no hace auto-login tras registro — intentamos navegar al
    # dashboard y, si redirige al login, hacemos login explícito con type()
    try:
        page.goto(EDITAR_CV, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
    except Exception as e:
        print(f"  [cht-pw] Error navegando al perfil: {e}")
        return

    if "chtlogin" in page.url or ("dashboard" not in page.url and "editar-cv" not in page.url):
        print(f"  [cht-pw] Sin sesion activa ({page.url}) — intentando login...")
        try:
            if "chtlogin" not in page.url:
                page.goto(f"{BASE_URL}/chtlogin", wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2000)
            page.locator("#username").wait_for(state="visible", timeout=10000)
            page.locator("#username").type(email, delay=80)
            page.wait_for_timeout(400)
            page.locator("#password").type(clave, delay=80)
            page.wait_for_timeout(400)
            page.locator("xpath=//input[@value='Iniciar Sesión'] | //button[@type='submit']").first.click()
            page.wait_for_timeout(5000)
            if "chtlogin" in page.url:
                print(f"  [cht-pw] ! Login falló — perfil sin completar")
                return
            print(f"  [cht-pw] Login OK: {page.url}")
            # Navegar al editor
            page.goto(EDITAR_CV, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
        except Exception as e:
            print(f"  [cht-pw] ! Error login: {e}")
            return

    if "dashboard" not in page.url and "editar-cv" not in page.url:
        print(f"  [cht-pw] ! No se pudo acceder al dashboard: {page.url}")
        return

    print(f"  [cht-pw] Perfil: {page.url}")

    rut     = str(user.get("RUT") or user.get("rut") or "")
    cel_raw = str(user.get("CELULAR") or user.get("celular") or "")
    cel     = _re.sub(r"\D", "", cel_raw)[-9:] if cel_raw else ""
    fn_raw  = str(user.get("FECHA_NACIMIENTO") or user.get("fecha_nacimiento") or "")
    try:
        ubs = user.get("UBICACIONES") or user.get("ubicaciones") or []
        if isinstance(ubs, str):
            ubs = _json.loads(ubs)
        ciudad = ubs[0].split(",")[0].strip() if ubs else "Santiago"
    except Exception:
        ciudad = "Santiago"

    profesion = str(user.get("PROFESION") or user.get("profesion") or "")
    resumen   = str(user.get("RESUMEN") or user.get("resumen") or "")
    exp_raw   = str(user.get("EXPERIENCIA") or user.get("experiencia") or "5")
    exp_num   = _re.sub(r"[^\d]", "", exp_raw) or "5"
    nivel     = str(user.get("NIVEL_EDUCATIVO") or user.get("nivel_educativo") or "")
    pret_raw  = str(user.get("PRETENSION_GENERAL") or user.get("pretension_general") or "")
    pret_num  = _re.sub(r"[^\d]", "", pret_raw) or ""
    try:
        cargos = user.get("CARGOS") or user.get("cargos") or []
        if isinstance(cargos, str):
            cargos = _json.loads(cargos)
        intereses = ", ".join(cargos[:3]) if cargos else ""
    except Exception:
        intereses = ""
    cv_url = str(user.get("CV_URL") or user.get("cv_url") or "")

    # ── Datos Personales ──────────────────────────────────────────────────────
    panel = _pw_abrir_sec(page, "Datos Personales")
    if panel:
        _pw_fill_sec(page, panel, "rut", rut)
        _pw_fill_sec(page, panel, "cel", cel)
        _pw_fill_sec(page, panel, "ciu", ciudad)
        if fn_raw:
            try:
                parts = fn_raw.split("-")
                ano, mes, dia = parts[0], parts[1].zfill(2), parts[2].zfill(2)
                _pw_select_sec(panel, "day",   dia,  [dia.lstrip("0"), str(int(dia))])
                _pw_select_sec(panel, "month", mes,  [mes.lstrip("0"), str(int(mes))])
                _pw_select_sec(panel, "year",  ano,  [])
            except Exception:
                pass
        _pw_guardar_sec(page, panel)

    # ── Educación y experiencia ───────────────────────────────────────────────
    panel = _pw_abrir_sec(page, "Educación y experiencia")
    if panel:
        exp_texto = resumen or f"Profesional con {exp_num} años de experiencia en {profesion}."
        _pw_fill_sec(page, panel, "exp", exp_texto[:500])
        carrera = str(user.get("CARRERA") or user.get("carrera") or profesion)
        _pw_fill_sec(page, panel, "resumen", carrera[:300])
        # anioexp: mayor valor <= exp_num
        try:
            opts = panel.locator("#anioexp option").all()
            vals = [o.get_attribute("value") for o in opts]
            target = exp_num if exp_num in vals else next(
                (v for v in reversed(vals) if v and v.isdigit() and int(v) <= int(exp_num)),
                vals[-1] if vals else "5"
            )
            panel.locator("#anioexp").select_option(value=target)
            print(f"  [cht-pw]   anioexp = {target}")
        except Exception:
            pass
        if nivel:
            try:
                opts = panel.locator("#nivel option").all()
                match = next(
                    (o.get_attribute("value") for o in opts
                     if nivel.upper() in o.inner_text().strip().upper()
                     or o.inner_text().strip().upper() in nivel.upper()), None
                )
                if match:
                    panel.locator("#nivel").select_option(value=match)
                    print(f"  [cht-pw]   nivel = {match}")
            except Exception:
                pass
        _pw_guardar_sec(page, panel)

    # ── Disponibilidad ────────────────────────────────────────────────────────
    panel = _pw_abrir_sec(page, "Disponibilidad y preferencias")
    if panel:
        if pret_num:
            _pw_fill_sec(page, panel, "pretensiones", pret_num)
        _pw_fill_sec(page, panel, "trabajociu", ciudad)
        if intereses:
            _pw_fill_sec(page, panel, "intereses", intereses[:200])
        _pw_guardar_sec(page, panel)

    # ── CV adjunto ────────────────────────────────────────────────────────────
    if cv_url:
        panel = _pw_abrir_sec(page, "Agregar / Editar CV")
        if panel:
            try:
                import tempfile, requests as _req, os as _os
                tmp_dir  = tempfile.mkdtemp()
                tmp_path = _os.path.join(tmp_dir, "CV.pdf")
                if "storage.googleapis.com" in cv_url or cv_url.startswith("gs://"):
                    from google.cloud import storage as gcs
                    parts = cv_url.replace("https://storage.googleapis.com/", "").split("/", 1)
                    gcs.Client().bucket(parts[0]).blob(parts[1]).download_to_filename(tmp_path)
                else:
                    r = _req.get(cv_url, timeout=30)
                    r.raise_for_status()
                    with open(tmp_path, "wb") as f:
                        f.write(r.content)
                print(f"  [cht-pw]   CV descargado ({_os.path.getsize(tmp_path)//1024} KB)")
                file_loc = panel.locator("input[type='file']").first
                if file_loc.count() == 0:
                    file_loc = page.locator("input[type='file']").first
                page.evaluate("el => { el.style.display='block'; el.style.visibility='visible'; }",
                              file_loc.element_handle())
                file_loc.set_input_files(tmp_path)
                page.wait_for_timeout(3000)
                print(f"  [cht-pw]   CV enviado")
                _pw_guardar_sec(page, panel)
            except Exception as e:
                print(f"  [cht-pw]   CV error: {e}")

    print(f"  [cht-pw] Perfil completado para {user_id}")


def _pw_crear_cuenta_chiletrabajos(user_id: str, user: dict) -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False

    nombre_completo = str(user.get("NOMBRE") or user.get("nombre") or "")
    partes   = nombre_completo.split()
    nombre   = partes[0] if partes else "Usuario"
    apellido = partes[1] if len(partes) > 1 else "Apellido"

    nombre_slug   = re.sub(r"[^a-z0-9]", "", nombre.lower())
    apellido_slug = re.sub(r"[^a-z0-9]", "", apellido.lower())[:3]
    codigo        = secrets.token_hex(3)
    email         = f"{nombre_slug}.{apellido_slug}{codigo}@gmail.com"
    clave         = _generar_clave()

    cloud = _cht_in_cloud_run()

    try:
        with sync_playwright() as pw:
            args = ["--no-sandbox", "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled"]
            if cloud:
                args.append("--single-process")
            browser = pw.chromium.launch(headless=True, args=args)
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 768},
            )
            page = ctx.new_page()

            for reg_url in [f"{BASE_URL}/chtregister", f"{BASE_URL}/registro", f"{BASE_URL}/crear-cuenta"]:
                page.goto(reg_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
                if "404" not in page.title().lower() and "login" not in page.url.lower():
                    print(f"  [cht-pw] Registro en: {page.url}")
                    break

            # Llenar campos
            _pw_fill(page,
                "input[name*='nombre' i]:visible, input[placeholder*='nombre' i]:visible, "
                "input[id*='nombre' i]:visible",
                nombre, "nombre")
            _pw_fill(page,
                "input[name*='apellido' i]:visible, input[placeholder*='apellido' i]:visible, "
                "input[id*='apellido' i]:visible",
                apellido, "apellido")
            _pw_fill(page,
                "input[type='email']:visible, input[name*='email' i]:visible, "
                "input[placeholder*='email' i]:visible",
                email, "email")

            pwd_locs = page.locator("input[type='password']:visible").all()
            for pl in pwd_locs:
                try:
                    pl.fill(clave)
                except Exception:
                    pass
            if pwd_locs:
                print(f"  [cht-pw] password: {len(pwd_locs)} campo(s)")

            page.wait_for_timeout(400)

            for chk in page.locator("input[type='checkbox']:visible").all():
                try:
                    if not chk.is_checked():
                        chk.check()
                except Exception:
                    pass

            submitted = False
            for sel in [
                "button[type='submit']:visible",
                "input[type='submit']:visible",
                "button:has-text('Registrar'):visible",
                "button:has-text('Crear'):visible",
                "button:visible",
            ]:
                try:
                    btn = page.locator(sel).first
                    if btn.count() > 0:
                        txt = btn.inner_text()[:40]
                        btn.click()
                        print(f"  [cht-pw] Submit: '{txt}'")
                        submitted = True
                        break
                except Exception:
                    pass

            if not submitted:
                print("  [cht-pw] ! No se encontró botón submit")
                browser.close()
                return False

            page.wait_for_timeout(5000)
            content = page.content().lower()
            cur = page.url

            error_signals = ["ya existe", "already registered", "ya está registrado", "correo en uso"]
            success_signals = [
                "cuenta creada", "registro exitoso", "bienvenido", "welcome",
                "tu cuenta", "verificar", "dashboard", "mi perfil", email.lower(),
            ]

            if any(s in content for s in error_signals):
                print(f"  [cht-pw] Email ya registrado — guardando credencial")
                bq.save_portal_cookies(user_id, PORTAL_ID, ctx.cookies(), email=email, password=clave)
                browser.close()
                return True

            if any(s in content for s in success_signals) or "chtlogin" not in cur:
                print(f"  [cht-pw] Cuenta creada OK para {user_id} ({email})")
                # Completar perfil en misma sesion
                if user:
                    _pw_perfil_misma_sesion(page, user, user_id, email=email, clave=clave)
                bq.save_portal_cookies(user_id, PORTAL_ID, ctx.cookies(), email=email, password=clave)
                browser.close()
                return True

            print(f"  [cht-pw] ! No confirmado. URL: {cur}")
            browser.close()
            return False

    except Exception as e:
        import traceback
        print(f"  [cht-pw] Error: {e}")
        traceback.print_exc()
        return False


def crear_cuenta_chiletrabajos(user_id: str, user: dict) -> bool:
    """Crea cuenta en ChileTrabajos. Playwright primero, Selenium como fallback."""
    cuenta = bq.get_portal_account(user_id, PORTAL_ID)
    if cuenta:
        print(f"  [cht] Cuenta ya existente para {user_id}: {cuenta['email']}")
        return True

    try:
        print(f"  [cht] Intentando Playwright...")
        if _pw_crear_cuenta_chiletrabajos(user_id, user):
            return True
        print(f"  [cht] Playwright sin éxito — fallback a Selenium...")
    except Exception as e:
        print(f"  [cht] Playwright error: {e} — fallback a Selenium...")
    return _selenium_crear_cuenta_chiletrabajos(user_id, user)


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
        crear_cuenta_chiletrabajos(u["ID_USUARIO"], u)
