"""
Indeed — Login y gestión de sesión.

Flujo:
  1. Navega a https://cl.indeed.com/account/login
  2. Ingresa email → contraseña
  3. Maneja verificación de identidad si Indeed la pide
  4. Guarda cookies en BigQuery para reutilizar sesión
  5. Reutiliza cookies guardadas en futuras postulaciones (evita re-login)

Uso directo (test):
    python indeed/login.py --user jobs2
"""
import os
import sys
import random
import time

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_in_cloud_run = bool(os.environ.get("K_SERVICE"))
HEADLESS = os.environ.get("PLAYWRIGHT_HEADLESS", "1" if _in_cloud_run else "0") != "0"

LOGIN_URL = "https://cl.indeed.com/account/login"
HOME_URL  = "https://cl.indeed.com"

# Selectores del formulario de login de Indeed
_SEL_EMAIL    = "input[name='__email'], input[type='email'], input[id*='email']"
_SEL_PASSWORD = "input[name='__password'], input[type='password']"
_SEL_CONTINUE = "button[type='submit'], button:has-text('Continuar'), button:has-text('Continue')"
_SEL_LOGGED   = "a[href*='/account'], a[href*='mi-perfil'], [data-testid='UserDropdown'], .icl-Dropdown"


def _human_type(page, selector: str, text: str) -> None:
    """Escribe carácter por carácter con delays variables para parecer humano."""
    el = page.locator(selector).first
    el.click()
    time.sleep(random.uniform(0.3, 0.6))
    for ch in text:
        page.keyboard.type(ch, delay=random.randint(70, 190))
    time.sleep(random.uniform(0.3, 0.5))


def _is_logged_in(page) -> bool:
    """Detecta si la sesión está activa buscando elementos del menú de usuario."""
    try:
        page.wait_for_selector(_SEL_LOGGED, timeout=4000)
        return True
    except Exception:
        pass
    # Verificación secundaria: la URL ya no es la de login
    return "account/login" not in page.url and "signin" not in page.url


def login_indeed(uid: str, email: str, password: str) -> list[dict] | None:
    """
    Hace login en Indeed y retorna las cookies de sesión.

    Si Indeed pide verificación por código (email OTP), muestra un prompt
    en consola para que el operador lo ingrese manualmente.

    Returns:
        Lista de cookies si el login fue exitoso, None si falló.
    """
    from playwright.sync_api import sync_playwright

    print(f"  [indeed] Iniciando login para {email}…")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=["--disable-blink-features=AutomationControlled", "--window-size=1280,800"],
        )
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-CL",
        )
        page = ctx.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )

        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
            time.sleep(random.uniform(2, 3))

            # ── paso 1: email ─────────────────────────────────────────────────
            try:
                page.wait_for_selector(_SEL_EMAIL, timeout=8000)
                _human_type(page, _SEL_EMAIL, email)
                time.sleep(random.uniform(0.5, 1.0))

                # Click en Continuar / Siguiente
                page.locator(_SEL_CONTINUE).first.click()
                time.sleep(random.uniform(1.5, 2.5))
            except Exception as e:
                print(f"  [indeed] Error en campo email: {e}")
                page.screenshot(path="indeed_login_debug.png")
                browser.close()
                return None

            # ── paso 2: contraseña ────────────────────────────────────────────
            try:
                page.wait_for_selector(_SEL_PASSWORD, timeout=8000)
                _human_type(page, _SEL_PASSWORD, password)
                time.sleep(random.uniform(0.5, 1.0))

                page.locator(_SEL_CONTINUE).first.click()
                print(f"  [indeed] Credenciales enviadas, esperando respuesta…")
                time.sleep(random.uniform(2, 3))
            except Exception:
                # Indeed puede saltar directo al OTP sin pedir contraseña
                pass

            # ── paso 3: verificación OTP (si Indeed la pide) ─────────────────
            # Esperar hasta 90 seg para que el operador ingrese el código o resuelva CAPTCHA
            for _ in range(90):
                if _is_logged_in(page):
                    break
                # ¿Hay campo de código de verificación?
                has_otp = False
                for otp_sel in ["input[name='__verificationCode']", "input[autocomplete='one-time-code']",
                                 "input[placeholder*='código']", "input[placeholder*='code']"]:
                    try:
                        page.locator(otp_sel).first.wait_for(timeout=500)
                        has_otp = True
                        break
                    except Exception:
                        pass

                if has_otp:
                    print("\n  ⚠️  Indeed pide código de verificación.")
                    print("  Revisa tu email y escríbelo aquí:")
                    code = input("  Código: ").strip()
                    if code:
                        try:
                            page.locator("input[name='__verificationCode'], input[autocomplete='one-time-code']").first.fill(code)
                            time.sleep(0.5)
                            page.locator(_SEL_CONTINUE).first.click()
                            time.sleep(2)
                        except Exception as e:
                            print(f"  [indeed] Error al ingresar OTP: {e}")
                    break

                time.sleep(1)

            # ── verificar sesión ──────────────────────────────────────────────
            if not _is_logged_in(page):
                page.screenshot(path="indeed_login_fail.png")
                print(f"  [indeed] Login fallido. Screenshot: indeed_login_fail.png")
                browser.close()
                return None

            print(f"  [indeed] Login exitoso. URL: {page.url}")

            # ── guardar cookies ───────────────────────────────────────────────
            raw_cookies = ctx.cookies()
            cookies = []
            for c in raw_cookies:
                entry = {
                    "name":   c["name"],
                    "value":  c["value"],
                    "domain": c.get("domain", ".indeed.com"),
                    "path":   c.get("path", "/"),
                }
                if c.get("expires") and c["expires"] > 0:
                    entry["expires"] = int(c["expires"])
                cookies.append(entry)

            browser.close()
            return cookies

        except Exception as e:
            import traceback
            print(f"  [indeed] Error inesperado: {e}")
            traceback.print_exc()
            browser.close()
            return None


def get_session(uid: str, email: str, password: str) -> list[dict] | None:
    """
    Retorna cookies de sesión Indeed válidas.

    Intenta primero las cookies guardadas en BigQuery.
    Si no existen o expiraron, hace login y guarda las nuevas.
    """
    import bq

    # Intentar cookies guardadas (máx 12 horas)
    saved = bq.get_portal_cookies(uid, "indeed", max_age_hours=12)
    if saved:
        print(f"  [indeed] Usando {len(saved)} cookies guardadas en BigQuery")
        return saved

    # Login fresco
    cookies = login_indeed(uid, email, password)
    if cookies:
        bq.save_portal_cookies(uid, "indeed", cookies)
        print(f"  [indeed] {len(cookies)} cookies guardadas en BigQuery")

    return cookies


def make_browser_context(cookies: list[dict] | None = None):
    """
    Retorna (playwright, browser, context) con sesión Indeed inyectada.

    Args:
        cookies: lista de cookies de get_session(). Si None, contexto anónimo.
    """
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=HEADLESS,
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="es-CL",
    )
    if cookies:
        try:
            ctx.add_cookies(cookies)
        except Exception as e:
            print(f"  [indeed] ⚠ cookies no inyectadas: {e}")
    return pw, browser, ctx


# ── CLI de test ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import json

    os.environ.setdefault(
        "GOOGLE_APPLICATION_CREDENTIALS",
        r"C:\Users\bastian\Desktop\Script_Python\jobs-425301-ba25295bbbd0.json",
    )

    parser = argparse.ArgumentParser(description="Test login Indeed")
    parser.add_argument("--user", default="jobs2", help="ID del usuario en BigQuery")
    parser.add_argument("--email",    default=None)
    parser.add_argument("--password", default=None)
    args = parser.parse_args()

    import bq
    cuenta = bq.get_portal_account(args.user, "indeed")
    if not cuenta and not (args.email and args.password):
        print(f"No hay cuenta Indeed guardada para {args.user}.")
        print("Pasa --email y --password para hacer login y guardar las credenciales.")
        sys.exit(1)

    email    = args.email    or cuenta["email"]
    password = args.password or cuenta["password"]

    cookies = login_indeed(args.user, email, password)
    if cookies:
        if not cuenta:
            bq.save_portal_account(args.user, "indeed", email, password)
        bq.save_portal_cookies(args.user, "indeed", cookies)
        print(f"\n✅ Login OK — {len(cookies)} cookies guardadas")
    else:
        print("\n❌ Login fallido")
        sys.exit(1)
