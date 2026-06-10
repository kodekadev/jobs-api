"""
Trabajando.cl — Completar perfil / onboarding.

Flujo:
  - Si hay cv_url → sube el PDF al portal (upload_cv_trabajando)
  - Si no hay PDF  → completa el wizard de CV interno con datos del perfil

Uso:
    from trabajando.completar_perfil import completar_perfil_trabajando
    ok = completar_perfil_trabajando(user_id="jobs2", user=perfil_dict)
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bq
from portal_accounts import upload_cv_trabajando, crear_cv_interno_trabajando

PORTAL_ID = "trabajando"


def completar_perfil_trabajando(user_id: str, user: dict) -> bool:
    """
    Sube el CV y/o completa el wizard de perfil en Trabajando.cl.

    Args:
        user_id : ID del usuario en BigQuery (ej: 'jobs2')
        user    : dict con CV_URL y datos del perfil

    Returns:
        True si el perfil quedó completo.
    """
    cuenta = bq.get_portal_account(user_id, PORTAL_ID)
    if not cuenta:
        print(f"  [tbj-perfil] Sin cuenta para {user_id} — ejecuta crear_cuenta primero")
        return False

    cv_url = user.get("cv_url") or user.get("CV_URL") or ""

    if cv_url:
        print(f"  [tbj-perfil] Subiendo CV archivo para {user_id}...")
        return upload_cv_trabajando(cuenta["email"], cuenta["password"], cv_url, user=user)
    else:
        print(f"  [tbj-perfil] Sin CV archivo — completando wizard interno para {user_id}...")
        return crear_cv_interno_trabajando(cuenta["email"], cuenta["password"], user=user)


if __name__ == "__main__":
    os.environ.setdefault(
        "GOOGLE_APPLICATION_CREDENTIALS",
        r"C:\Users\bastian\Desktop\Script_Python\jobs-425301-ba25295bbbd0.json",
    )
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
    users = bq.get_active_users()
    for u in users:
        completar_perfil_trabajando(u["ID_USUARIO"], u)
