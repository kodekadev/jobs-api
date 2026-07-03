"""
Guarda la sesión de Trabajando.cl de un usuario para postulaciones automáticas.

Uso:
    python save_trabajando_session.py jobs2

Flujo:
  1. Abre Chrome visible
  2. Navega a trabajando.cl/ingresa-a-tu-cuenta
  3. El usuario inicia sesión manualmente (resuelve el reCAPTCHA en el browser)
  4. Guarda las cookies en BigQuery bajo el usuario dado (TTL 120h)
"""

import sys
import time

from playwright.sync_api import sync_playwright

import bq


def save_trabajando_session(user_id: str) -> None:
    # Obtener el email registrado para este usuario
    creds = bq.get_portal_account(user_id, "trabajando")
    email_hint = creds["email"] if creds else ""

    print(f"\n[Trabajando] Guardando sesión para usuario: {user_id}")
    if email_hint:
        print(f"  Email registrado: {email_hint}")
    print("Se abrirá un navegador. Inicia sesión en Trabajando.cl y espera...\n")

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

        if email_hint:
            print(f"→ Rellena el formulario con el email '{email_hint}' y la contraseña guardada.")
        print("→ Resuelve el reCAPTCHA si aparece y haz click en 'Entrar'.")
        print("  (El script detectará automáticamente cuando estés logueado)\n")

        logged_in = False
        for i in range(120):  # hasta ~6 minutos
            time.sleep(3)
            try:
                cur = page.url
                if "ingresa-a-tu-cuenta" not in cur and "trabajando.cl" in cur:
                    logged_in = True
                    break
                if "/checkpoint/" in cur:
                    print("  Trabajando pidió verificación adicional. Complétala en el navegador.")
            except Exception:
                pass

            if (i + 1) % 5 == 0:
                print(f"  Esperando... {(i+1)*3}s")

        if not logged_in:
            print("\n✗ No se detectó login en 6 minutos. Abortando.")
            browser.close()
            return

        print(f"\n✓ Login detectado ({page.url}). Guardando cookies...")

        all_cookies = ctx.cookies(urls=["https://www.trabajando.cl"])
        if not all_cookies:
            # fallback a todas las cookies
            all_cookies = ctx.cookies()
            all_cookies = [c for c in all_cookies if "trabajando" in c.get("domain", "")]

        browser.close()

    if not all_cookies:
        print("✗ No se encontraron cookies de sesión.")
        return

    print(f"  {len(all_cookies)} cookies capturadas.")

    bq.save_portal_cookies(
        user_id=user_id,
        portal="trabajando",
        cookies=all_cookies,
        email=email_hint,
        password=creds["password"] if creds else "",
    )
    print(f"✓ Sesión de Trabajando.cl guardada en BigQuery para '{user_id}'")
    print("  Las cookies duran 120h antes de que el sistema necesite renovarlas.")
    print(f"\nPuedes ejecutar el backend ahora:")
    print(f"  python main.py  (o  SINGLE_USER_ID={user_id} python main.py)")


if __name__ == "__main__":
    uid = sys.argv[1] if len(sys.argv) > 1 else "jobs2"
    save_trabajando_session(uid)
