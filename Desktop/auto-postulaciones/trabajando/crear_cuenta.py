"""
Trabajando.cl — Creación de cuenta nueva.

Flujo:
  1. Verifica si el usuario ya tiene cuenta en BigQuery CUENTAS_PORTALES
  2. Si no tiene → crea cuenta en trabajando.cl con Selenium
  3. Guarda credenciales en BigQuery

Uso:
    from trabajando.crear_cuenta import crear_cuenta_trabajando
    ok = crear_cuenta_trabajando(user_id="jobs2", user=perfil_dict)
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bq
from portal_accounts import get_or_create_account

PORTAL_ID = "trabajando"


def crear_cuenta_trabajando(user_id: str, user: dict) -> bool:
    """
    Crea o recupera la cuenta de trabajando.cl para el usuario.
    Guarda email y contraseña en BigQuery CUENTAS_PORTALES.

    Args:
        user_id : ID del usuario en BigQuery (ej: 'jobs2')
        user    : dict con NOMBRE, CELULAR y datos del perfil

    Returns:
        True si la cuenta está lista (nueva o existente).
    """
    cuenta = get_or_create_account(user, PORTAL_ID)
    if cuenta:
        print(f"  [tbj] Cuenta lista para {user_id}: {cuenta['email']}")
        return True
    print(f"  [tbj] No se pudo crear cuenta para {user_id}")
    return False


if __name__ == "__main__":
    os.environ.setdefault(
        "GOOGLE_APPLICATION_CREDENTIALS",
        r"C:\Users\bastian\Desktop\Script_Python\jobs-425301-ba25295bbbd0.json",
    )
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
    users = bq.get_active_users()
    for u in users:
        crear_cuenta_trabajando(u["ID_USUARIO"], u)
