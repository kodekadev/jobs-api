"""
Migración BQ: agrega perfil_completado_at y perfil_hash a CUENTAS_PORTALES.
Corre una sola vez.
"""
import os
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")

from google.cloud import bigquery

PROJECT = "jobs-425301"
DATASET = "DWH"
client  = bigquery.Client(project=PROJECT)


def run(sql):
    print(f"  >> {sql[:100]}")
    client.query(sql).result()
    print("     OK")


print("=== Migrando CUENTAS_PORTALES ===")

run(f"""
    ALTER TABLE `{PROJECT}.{DATASET}.CUENTAS_PORTALES`
    ADD COLUMN IF NOT EXISTS perfil_completado_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS perfil_hash STRING
""")

print("=== Migración completada ===")
