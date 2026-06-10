"""
Trabajando.cl — Postulación automática.

Flujo:
  1. Recibe una Playwright Page ya autenticada (desde login.get_session)
  2. Navega al empleo y hace click en "Postular"
  3. Responde preguntas del formulario con datos del perfil / LLM
  4. Retorna dict con empresa y descripción, o False si falló

Uso:
    from trabajando.login import get_session, close_session
    from trabajando.postular import postular

    page, tipo = get_session(user_id, email, password)
    resultado  = postular(page, job_url, user)
    close_session(user_id)
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from portal_accounts import apply_trabajando_playwright


def postular(page, job_url: str, user: dict, resumen: str = "", job_title: str = "") -> "dict | bool":
    """
    Postula a un empleo en Trabajando.cl usando una sesión Playwright activa.

    Args:
        page      : Playwright Page autenticada (de trabajando.login.get_session)
        job_url   : URL del aviso en trabajando.cl
        user      : dict con datos del perfil del candidato
        resumen   : resumen/cover letter opcional
        job_title : título del cargo para contexto LLM

    Returns:
        dict {"ok": True, "empresa": ..., "descripcion": ...} o False.
    """
    return apply_trabajando_playwright(
        page,
        job_url,
        user=user,
        resumen=resumen,
        job_title=job_title,
    )


if __name__ == "__main__":
    import argparse

    os.environ.setdefault(
        "GOOGLE_APPLICATION_CREDENTIALS",
        r"C:\Users\bastian\Desktop\Script_Python\jobs-425301-ba25295bbbd0.json",
    )
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))

    import bq
    from trabajando.login import get_session, close_session

    parser = argparse.ArgumentParser(description="Test postulación Trabajando.cl")
    parser.add_argument("--user", required=True, help="ID de usuario")
    parser.add_argument("--url",  required=True, help="URL del empleo en trabajando.cl")
    args = parser.parse_args()

    users = bq.get_user_by_id(args.user)
    if not users:
        print(f"Usuario {args.user} no encontrado")
        raise SystemExit(1)

    u = users[0]
    cuenta = bq.get_portal_account(args.user, "trabajando")
    if not cuenta:
        print("Sin cuenta en trabajando.cl — ejecuta crear_cuenta primero")
        raise SystemExit(1)

    page, tipo = get_session(args.user, cuenta["email"], cuenta["password"])
    if not page or tipo != "playwright":
        print("Se requiere sesión Playwright para postular")
        raise SystemExit(1)

    resultado = postular(page, args.url, u)
    print(f"Resultado: {resultado}")
    close_session(args.user)
