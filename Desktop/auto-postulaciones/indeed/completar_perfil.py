"""
Indeed — Completar perfil.

Indeed Easy Apply no usa un perfil almacenado en el portal.
Los datos del candidato (nombre, email, teléfono, CV) se envían
directamente en cada postulación desde el perfil de BigQuery.

Este módulo existe solo para mantener la estructura uniforme entre portales.
"""


def completar_perfil_indeed(user_id: str, user: dict) -> bool:
    """No aplica — Indeed Easy Apply no tiene perfil en el portal."""
    print("  [indeed] No se requiere completar perfil en Indeed Easy Apply")
    return True
