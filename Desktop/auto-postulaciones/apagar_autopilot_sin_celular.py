"""
Apaga el autopilot (activo=0) de todos los usuarios que no tienen celular.
Ejecutar una sola vez: python apagar_autopilot_sin_celular.py
"""
import os, sys

_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else r"C:\Users\bastian\Desktop\auto-postulaciones"
if _dir not in sys.path:
    sys.path.insert(0, _dir)

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")

from dotenv import load_dotenv
load_dotenv(os.path.join(_dir, ".env"))

import bq

PROJECT = bq.PROJECT
DATASET = bq.DATASET

# 1. Ver quiénes serán afectados
query_preview = f"""
    SELECT pa.id_usuario, u.NOMBRE, u.EMAIL, u.CELULAR
    FROM `{PROJECT}.{DATASET}.POSTULACIONES_AUTO` pa
    LEFT JOIN `{PROJECT}.{DATASET}.USUARIOS` u ON LOWER(u.ID_USUARIO) = LOWER(pa.id_usuario)
    WHERE pa.activo = 1
      AND COALESCE(TRIM(u.CELULAR), '') = ''
    ORDER BY pa.id_usuario
"""

print("Usuarios con autopilot activo y sin celular:")
rows = list(bq._query(query_preview).result())
if not rows:
    print("  Ninguno — nada que hacer.")
    sys.exit(0)

for r in rows:
    print(f"  {r['id_usuario']:10s} | {(r['NOMBRE'] or '')[:30]:30s} | {r['EMAIL'] or ''}")

print(f"\nTotal: {len(rows)} usuario(s)")

confirm = input("\n¿Apagar autopilot de estos usuarios? [s/N]: ").strip().lower()
if confirm != "s":
    print("Cancelado.")
    sys.exit(0)

# 2. Apagar
query_update = f"""
    UPDATE `{PROJECT}.{DATASET}.POSTULACIONES_AUTO` pa
    SET activo = 0,
        fecha_actualizacion = CURRENT_TIMESTAMP()
    WHERE pa.activo = 1
      AND EXISTS (
        SELECT 1
        FROM `{PROJECT}.{DATASET}.USUARIOS` u
        WHERE LOWER(u.ID_USUARIO) = LOWER(pa.id_usuario)
          AND COALESCE(TRIM(u.CELULAR), '') = ''
      )
"""

job = bq._query(query_update)
job.result()
print(f"\nListo — autopilot apagado para {len(rows)} usuario(s).")
