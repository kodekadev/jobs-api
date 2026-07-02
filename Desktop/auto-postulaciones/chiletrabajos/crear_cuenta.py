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

def crear_cuenta_chiletrabajos(user_id: str, user: dict) -> bool:
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
