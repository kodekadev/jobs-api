"""
Trabajando.cl — Login y gestión de sesión.

Estrategia:
  1. Intenta Playwright con cookies guardadas en BigQuery (evita CAPTCHA)
  2. Si no hay cookies → cae a Selenium con undetected-chromedriver

Ejecutar localmente para refrescar cookies (Cloud Run las reutiliza):
    python -m trabajando.login              # todos los usuarios activos
    python -m trabajando.login --user jobs2 # solo un usuario

Uso programático:
    from trabajando.login import get_session, close_session
    page = get_session(user_id, email, password)   # retorna Playwright page
    close_session(user_id)
"""
import os
import sys
import argparse

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bq
from portal_accounts import (
    get_trabajando_pw_session,
    get_trabajando_session,
    close_trabajando_session,
)

PORTAL_ID = "trabajando"


def get_session(user_id: str, email: str, password: str):
    """
    Retorna sesión autenticada en trabajando.cl.
    Intenta Playwright primero; si falla, retorna driver Selenium.

    Returns:
        (page, "playwright") o (driver, "selenium") o (None, None) si ambos fallan.
    """
    page = get_trabajando_pw_session(user_id, email, password)
    if page:
        print(f"  [tbj-login] Sesión Playwright OK para {user_id}")
        return page, "playwright"

    print(f"  [tbj-login] Playwright falló — intentando Selenium para {user_id}")
    driver = get_trabajando_session(user_id, email, password)
    if driver:
        print(f"  [tbj-login] Sesión Selenium OK para {user_id}")
        return driver, "selenium"

    print(f"  [tbj-login] ! No se pudo iniciar sesión para {user_id}")
    return None, None


def close_session(user_id: str) -> None:
    """Cierra y limpia la sesión activa del usuario."""
    close_trabajando_session(user_id)


if __name__ == "__main__":
    os.environ.setdefault(
        "GOOGLE_APPLICATION_CREDENTIALS",
        r"C:\Users\bastian\Desktop\Script_Python\jobs-425301-ba25295bbbd0.json",
    )
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))

    parser = argparse.ArgumentParser(description="Login Trabajando.cl")
    parser.add_argument("--user", help="ID de usuario específico")
    args = parser.parse_args()

    users = bq.get_active_users()
    if args.user:
        users = [u for u in users if u["ID_USUARIO"].lower() == args.user.lower()]

    for u in users:
        uid = u["ID_USUARIO"]
        cuenta = bq.get_portal_account(uid, PORTAL_ID)
        if not cuenta:
            print(f"[{uid}] Sin cuenta en {PORTAL_ID}")
            continue
        session, tipo = get_session(uid, cuenta["email"], cuenta["password"])
        if session:
            print(f"[{uid}] Login OK ({tipo})")
            close_session(uid)
        else:
            print(f"[{uid}] Login FALLÓ")
