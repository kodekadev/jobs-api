"""
Guarda la sesion de Trabajando.cl de un usuario para postulaciones automaticas.

Uso:
    python save_trabajando_session.py jobs2

Flujo:
  1. Abre Chrome visible
  2. Navega a trabajando.cl/ingresa-a-tu-cuenta
  3. Auto-rellena email y password
  4. El usuario solo resuelve el reCAPTCHA y hace click en 'Entrar'
  5. Guarda las cookies en BigQuery (TTL 120h)
"""

import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

import bq


def save_trabajando_session(user_id: str) -> None:
    creds = bq.get_portal_account(user_id, "trabajando")
    if not creds:
        print(f"[ERROR] No hay cuenta de Trabajando.cl para '{user_id}' en BigQuery.")
        return

    email    = creds["email"]
    password = creds["password"]

    print(f"\n[Trabajando] Guardando sesion para usuario: {user_id}")
    print(f"  Email: {email}")
    print("Abriendo navegador...\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport=None,
        )
        page = ctx.new_page()
        page.goto("https://www.trabajando.cl/ingresa-a-tu-cuenta", timeout=20000)
        page.wait_for_load_state("networkidle", timeout=10000)

        # Auto-rellenar email y password
        try:
            page.fill('input[type="email"], input[name="email"], input[id*="email"]', email, timeout=5000)
            page.fill('input[type="password"]', password, timeout=5000)
            print("-> Email y password rellenados automaticamente.")
        except Exception as e:
            print(f"  Aviso: no se pudo auto-rellenar ({e}). Ingresa los datos manualmente.")

        # Intentar click automatico en el boton de submit
        try:
            btn = page.locator('button[type="submit"], button:has-text("Entrar"), input[type="submit"]').first
            btn.click(timeout=3000)
            print("-> Click en 'Entrar' automatico. Esperando respuesta...")
            time.sleep(5)
            cur = page.url
            if any(s in cur for s in ["cuenta-creada", "/mi-cuenta", "/home", "/empleos", "/perfil", "/buscar"]) \
                    or ("ingresa-a-tu-cuenta" not in cur and "trabajando.cl" in cur):
                print("-> Login automatico exitoso (sin CAPTCHA).")
            else:
                print("-> Login automatico no redireccionó. Posible CAPTCHA.")
                print("   Resuelve el reCAPTCHA y haz click en 'Entrar' manualmente.")
        except Exception as e:
            print(f"  Click automatico fallo ({e}). Haz click en 'Entrar' manualmente.")
            print("-> Resuelve el reCAPTCHA si aparece y haz click en 'Entrar'.")

        print("  (El script detectara cuando estes logueado)\n")

        _LOGIN_URL = "ingresa-a-tu-cuenta"
        _SUCCESS_PATHS = ["cuenta-creada", "/mi-cuenta", "/home", "/empleos", "/perfil", "/buscar"]

        logged_in = False
        for i in range(120):  # hasta ~6 minutos
            time.sleep(3)
            try:
                cur = page.url
                # Exito: salimos de la pagina de login O llegamos a sub-path de exito
                if any(s in cur for s in _SUCCESS_PATHS):
                    logged_in = True
                    break
                if _LOGIN_URL not in cur and "trabajando.cl" in cur:
                    logged_in = True
                    break
                if "/checkpoint/" in cur:
                    print("  Trabajando pidio verificacion adicional. Completala en el navegador.")
            except Exception:
                pass

            if (i + 1) % 5 == 0:
                print(f"  Esperando... {(i+1)*3}s")

        if not logged_in:
            print("\nERROR: No se detecto login en 6 minutos. Abortando.")
            browser.close()
            return

        print(f"\nOK Login detectado ({page.url}). Guardando cookies...")

        all_cookies = ctx.cookies(urls=["https://www.trabajando.cl"])
        if not all_cookies:
            all_cookies = ctx.cookies()
            all_cookies = [c for c in all_cookies if "trabajando" in c.get("domain", "")]

        browser.close()

    if not all_cookies:
        print("ERROR: No se encontraron cookies de sesion.")
        return

    print(f"  {len(all_cookies)} cookies capturadas.")

    bq.save_portal_cookies(
        user_id=user_id,
        portal="trabajando",
        cookies=all_cookies,
        email=email,
        password=password,
    )
    print(f"OK Sesion de Trabajando.cl guardada en BigQuery para '{user_id}'")
    print("  Las cookies duran 120h antes de que el sistema necesite renovarlas.")
    print(f"\nPuedes ejecutar: SINGLE_USER_ID={user_id} python main.py")


if __name__ == "__main__":
    uid = sys.argv[1] if len(sys.argv) > 1 else "jobs2"
    save_trabajando_session(uid)
