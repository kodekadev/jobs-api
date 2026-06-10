"""
Indeed — Postulación automática via Easy Apply.

Flujo:
  1. Abre el aviso de empleo con Playwright (sesión anónima)
  2. Detecta el botón "Aplicar fácilmente" / "Apply now"
  3. Recorre los pasos del wizard: nombre, email, teléfono, CV, preguntas
  4. Envía la solicitud

No requiere cuenta ni sesión previa — usa los datos del perfil de BigQuery.

Uso:
    from indeed.postular import postular
    ok = postular(job_url, user)
"""
import os
import sys
import argparse

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from applier import apply_indeed


def postular(job_url: str, user: dict) -> bool:
    """
    Postula a un empleo de Indeed via Easy Apply.

    Args:
        job_url : URL del aviso en indeed.com
        user    : dict con NOMBRE, EMAIL, CELULAR, cv_url del perfil

    Returns:
        True si la postulación se envió correctamente.
    """
    return apply_indeed(user, job_url)


if __name__ == "__main__":
    os.environ.setdefault(
        "GOOGLE_APPLICATION_CREDENTIALS",
        r"C:\Users\bastian\Desktop\Script_Python\jobs-425301-ba25295bbbd0.json",
    )
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))

    import bq

    parser = argparse.ArgumentParser(description="Test postulación Indeed Easy Apply")
    parser.add_argument("--user", required=True, help="ID de usuario en BigQuery")
    parser.add_argument("--url",  required=True, help="URL del empleo en indeed.com")
    args = parser.parse_args()

    users = bq.get_user_by_id(args.user)
    if not users:
        print(f"Usuario {args.user} no encontrado")
        raise SystemExit(1)

    ok = postular(args.url, users[0])
    print(f"Resultado: {'OK' if ok else 'FALLÓ'}")
