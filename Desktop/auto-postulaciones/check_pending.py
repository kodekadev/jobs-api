import os
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = r'C:\Users\bastian\.secrets\google\credenciales.json'
import bq

q = f"""
    SELECT U.ID_USUARIO, U.NOMBRE
    FROM `{bq.PROJECT}.{bq.DATASET}.USUARIOS` U
    JOIN `{bq.PROJECT}.{bq.DATASET}.POSTULA_FACIL` P ON U.ID_USUARIO = P.ID_USUARIO
    LEFT JOIN `{bq.PROJECT}.{bq.DATASET}.CUENTAS_PORTALES` C
        ON U.ID_USUARIO = C.id_usuario AND C.portal = 'trabajando'
    WHERE C.id_usuario IS NULL
"""
rows = list(bq.client.query(q).result())
print(f"Usuarios pendientes: {len(rows)}")
for r in rows:
    print(f"  {r.ID_USUARIO} - {r.NOMBRE}")
