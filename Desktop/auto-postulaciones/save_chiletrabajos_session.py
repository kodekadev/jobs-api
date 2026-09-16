"""
Guarda la sesión de ChileTrabajos de un usuario y completa su perfil.

Corre LOCALMENTE con Chrome visible (no en Cloud Run).
ChileTrabajos bloquea logins headless — se necesita Chrome real.

Uso:
    python save_chiletrabajos_session.py jobs8

Flujo:
  1. Lee credenciales de BigQuery (si existen)
  2. Si no hay cuenta: crea una con Selenium (Chrome real)
  3. Si hay cuenta pero login falla: recrea la cuenta
  4. Completa las secciones del perfil en /dashboard/editar-cv
  5. Guarda cookies en BigQuery para que Cloud Run las use en postulaciones
"""

import sys
import os
import re
import time
import secrets
import string

# Spyder/IPython reemplazan sys.stdout por un stream sin reconfigure()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bq
from chiletrabajos.login import _do_login
from chiletrabajos.completar_perfil import (
    _fill_datos_personales,
    _fill_educacion_experiencia,
    _fill_disponibilidad,
    _upload_cv,
)

BASE_URL  = "https://www.chiletrabajos.cl"
PORTAL_ID = "chiletrabajos"


def _make_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    options = Options()
    options.add_argument("--window-size=1366,768")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
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


def _crear_cuenta_selenium(driver, nombre: str, apellido: str, email: str, clave: str) -> bool:
    """Crea cuenta en ChileTrabajos usando Selenium."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    def _js_set(el, value):
        driver.execute_script("""
            var el=arguments[0], v=arguments[1];
            var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');
            if(s) s.set.call(el,v);
            el.dispatchEvent(new Event('input',{bubbles:true}));
            el.dispatchEvent(new Event('change',{bubbles:true}));
        """, el, value)

    def _fill(kws, value, label):
        for kw in kws:
            els = [i for i in driver.find_elements(By.TAG_NAME, "input")
                   if i.is_displayed()
                   and kw.lower() in (i.get_attribute("placeholder") or "").lower()
                   + (i.get_attribute("name") or "").lower()
                   + (i.get_attribute("id") or "").lower()]
            if els:
                _js_set(els[0], value)
                print(f"  [cht] {label}: '{value[:30]}'")
                return True
        return False

    try:
        driver.get(f"{BASE_URL}/chtregister")
        time.sleep(3)
        print(f"  [cht] Registro en: {driver.current_url}")

        _fill(["nombre", "first", "name"],      nombre,   "nombre")
        _fill(["apellido", "last", "surname"],   apellido, "apellido")
        _fill(["email", "correo", "mail"],       email,    "email")

        pwd_fields = [i for i in driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
                      if i.is_displayed()]
        for pf in pwd_fields:
            _js_set(pf, clave)
        if pwd_fields:
            print(f"  [cht] password: {len(pwd_fields)} campo(s)")

        time.sleep(0.5)
        for chk in driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']"):
            try:
                if chk.is_displayed() and not chk.is_selected():
                    driver.execute_script("arguments[0].click();", chk)
            except Exception:
                pass

        for xp in ["//button[@type='submit']", "//input[@type='submit']",
                   "//button[contains(translate(.,'REGISTRARSE','registrarse'),'registrar')]",
                   "//button[contains(translate(.,'CREAR CUENTA','crear cuenta'),'crear')]"]:
            btns = [b for b in driver.find_elements(By.XPATH, xp) if b.is_displayed()]
            if btns:
                btn_text = ""
                try:
                    btn_text = (btns[0].text or "").strip()[:30]
                except Exception:
                    pass
                driver.execute_script("arguments[0].click();", btns[0])
                print(f"  [cht] Submit: '{btn_text}'")
                break

        time.sleep(6)
        content = driver.page_source.lower()
        cur = driver.current_url

        if any(s in content for s in ["ya existe", "already registered", "correo en uso"]):
            print(f"  [cht] Email ya registrado — usando credenciales guardadas")
            return True
        if any(s in content for s in ["cuenta creada", "bienvenido", "dashboard", email.lower()]) \
                or "chtlogin" not in cur:
            print(f"  [cht] Cuenta creada OK. URL: {cur}")
            return True

        print(f"  [cht] ! Registro no confirmado. URL: {cur}")
        return False

    except Exception as e:
        import traceback
        print(f"  [cht] Error registro: {e}")
        traceback.print_exc()
        return False


def save_session(user_id: str):
    users = bq.get_user_by_id(user_id)
    if not users:
        print(f"ERROR: usuario {user_id} no encontrado en BigQuery")
        return False

    user   = users[0]
    nombre_completo = str(user.get("NOMBRE") or user.get("nombre") or "")
    partes   = nombre_completo.split()
    nombre   = partes[0] if partes else "Usuario"
    apellido = partes[1] if len(partes) > 1 else "Apellido"
    nombre_slug   = re.sub(r"[^a-z0-9]", "", nombre.lower())
    apellido_slug = re.sub(r"[^a-z0-9]", "", apellido.lower())[:3]

    print(f"\n[{nombre_completo}] ChileTrabajos — guardar sesión")

    cuenta = bq.get_portal_account(user_id, PORTAL_ID)
    driver = _make_driver()

    try:
        # ── 1. Intentar login con cuenta existente ────────────────────────────
        if cuenta:
            email    = cuenta["email"]
            password = cuenta["password"]
            print(f"  Cuenta existente: {email}")
            print(f"  Intentando login...")
            ok = _do_login(driver, email, password)
            if ok:
                print(f"  Login OK: {driver.current_url}")
            else:
                print(f"  Login falló — posible contraseña incorrecta (registro vía Playwright)")
                print(f"  Creando cuenta nueva con Selenium...")
                cuenta = None  # Forzar creación nueva

        # ── 2. Crear cuenta si no existe o login falló ────────────────────────
        if not cuenta:
            codigo  = secrets.token_hex(3)
            email   = f"{nombre_slug}.{apellido_slug}{codigo}@gmail.com"
            clave   = _generar_clave()

            # Borrar registro anterior si existe
            try:
                bq._query(
                    f"DELETE FROM `jobs-425301.DWH.CUENTAS_PORTALES` "
                    f"WHERE LOWER(ID_USUARIO)='{user_id.lower()}' AND LOWER(PORTAL)='{PORTAL_ID}'"
                ).result()
            except Exception:
                pass

            if not _crear_cuenta_selenium(driver, nombre, apellido, email, clave):
                print(f"  ERROR: No se pudo crear la cuenta")
                driver.quit()
                return False

            bq.save_portal_account(user_id, PORTAL_ID, email, clave)
            print(f"  Cuenta guardada en BQ: {email}")

            # ChileTrabajos auto-loguea tras el registro — verificar si ya hay sesión
            cur_url = driver.current_url
            if "dashboard" in cur_url or "editar-cv" in cur_url:
                print(f"  Ya logueado tras registro: {cur_url}")
            else:
                print(f"  Haciendo login...")
                if not _do_login(driver, email, clave):
                    print(f"  ERROR: Login falló tras crear cuenta")
                    driver.quit()
                    return False
                print(f"  Login OK: {driver.current_url}")
            password = clave

        # ── 3. Completar perfil ───────────────────────────────────────────────
        driver.get(f"{BASE_URL}/dashboard/editar-cv")
        time.sleep(4)
        print(f"  En: {driver.current_url}")

        _fill_datos_personales(driver, user)
        _fill_educacion_experiencia(driver, user)
        _fill_disponibilidad(driver, user)
        _upload_cv(driver, user)

        # ── 4. Guardar cookies ────────────────────────────────────────────────
        cookies = driver.get_cookies()
        bq.save_portal_cookies(user_id, PORTAL_ID, cookies,
                               email=email, password=password)
        print(f"\n  Cookies guardadas ({len(cookies)} cookies)")
        print(f"  ✓ {user_id} listo para postular en ChileTrabajos")

        driver.quit()
        return True

    except Exception as e:
        import traceback
        print(f"  ERROR: {e}")
        traceback.print_exc()
        try:
            driver.quit()
        except Exception:
            pass
        return False


def main():
    if len(sys.argv) < 2:
        print("Uso: python save_chiletrabajos_session.py <user_id>")
        print("Ejemplo: python save_chiletrabajos_session.py jobs8")
        sys.exit(1)

    user_id = sys.argv[1].strip()
    ok = save_session(user_id)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
