"""
Batch: crea cuentas en portales para todos los usuarios que tienen
POSTULA_FACIL completo pero no tienen cuenta en CUENTAS_PORTALES.
"""
import os
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = r'C:\Users\bastian\.secrets\google\credenciales.json'
os.environ['PYTHONIOENCODING'] = 'utf-8'

import bq
from portal_accounts import get_or_create_account

PORTALES = ["trabajando"]

def main():
    print("Buscando usuarios sin cuenta en portales...")

    rows = list(bq.client.query(f"""
        SELECT U.ID_USUARIO, U.NOMBRE, U.EMAIL, U.CELULAR
        FROM `{bq.PROJECT}.{bq.DATASET}.USUARIOS` U
        JOIN `{bq.PROJECT}.{bq.DATASET}.POSTULA_FACIL` P
            ON U.ID_USUARIO = P.ID_USUARIO
        LEFT JOIN `{bq.PROJECT}.{bq.DATASET}.CUENTAS_PORTALES` C
            ON U.ID_USUARIO = C.id_usuario AND C.portal = 'trabajando'
        WHERE C.id_usuario IS NULL
    """).result())

    print(f"Usuarios pendientes: {len(rows)}\n")

    ok, err = 0, 0
    for i, row in enumerate(rows):
        user = dict(row)
        nombre = user.get('NOMBRE') or user.get('ID_USUARIO')
        print(f"[{i+1}/{len(rows)}] {nombre}")
        for portal in PORTALES:
            cuenta = get_or_create_account(user, portal)
            if cuenta:
                ok += 1
                print(f"  -> {portal}: OK ({cuenta['email']})")
            else:
                err += 1
                print(f"  -> {portal}: FALLO")

    print(f"\nFinalizado: {ok} OK, {err} errores")

if __name__ == "__main__":
    main()
