"""
Script Jenkins — secuencia de emails TRIAL → PRO.

Corre diariamente. Envía:
  - Día -3 y día -1: aviso de vencimiento próximo (solo usuarios con ≥ UMBRAL postulaciones)
  - Día 0: email de reactivación (versión completa si N≥UMBRAL, versión corta si N<UMBRAL)

Variables de entorno requeridas: RESEND_API_KEY, APP_URL (opcional, default aplicai.cl)
"""

import os
import sys

# Asegurar imports desde la carpeta del script
sys.path.insert(0, os.path.dirname(__file__))

import bq
import notifier

UMBRAL = 5  # usuarios con ≥5 postulaciones reciben la secuencia completa de conversión


def _run_day(days: int, dry_run: bool = False) -> None:
    """Procesa usuarios cuyo trial vence en `days` días."""
    print(f"\n--- Trial conversion: día -{days} ---")
    users = bq.get_expiring_trials(days=days)
    print(f"  Usuarios encontrados: {len(users)}")

    for user in users:
        uid    = user.get("ID_USUARIO") or user.get("id_usuario", "")
        nombre = user.get("NOMBRE") or user.get("nombre", uid)
        n      = bq.get_total_postulaciones(uid)
        print(f"  [{uid}] {nombre} — {n} postulaciones")

        if n >= UMBRAL:
            if dry_run:
                print(f"  [DRY] conversion dia -{days} -> {uid}")
            else:
                notifier.send_trial_conversion(user, days, n)
        else:
            print(f"  [{uid}] N<{UMBRAL} — sin email día -{days} (no ha visto valor suficiente)")


def _run_day_0(dry_run: bool = False) -> None:
    """Procesa usuarios cuyo trial expiró hoy."""
    print("\n--- Trial conversion: día 0 (expirado) ---")
    users = bq.get_expired_trials_today()
    print(f"  Usuarios encontrados: {len(users)}")

    for user in users:
        uid    = user.get("ID_USUARIO") or user.get("id_usuario", "")
        nombre = user.get("NOMBRE") or user.get("nombre", uid)
        n      = bq.get_total_postulaciones(uid)
        print(f"  [{uid}] {nombre} — {n} postulaciones")

        if dry_run:
            _cual = "expired" if n >= UMBRAL else "expired_low_usage"
            print(f"  [DRY] {_cual} -> {uid}")
        elif n >= UMBRAL:
            notifier.send_trial_expired(user, n)
        else:
            notifier.send_trial_expired_low_usage(user)


if __name__ == "__main__":
    import sys
    _dry = "--dry-run" in sys.argv
    if _dry:
        print("[dry-run] simulacion: no se envia ningun correo")
    _run_day(3, _dry)
    _run_day(1, _dry)
    _run_day_0(_dry)
    print("Done.")
