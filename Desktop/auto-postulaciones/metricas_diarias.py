"""
Registro diario de ofertas compatibles vs postuladas, por usuario y portal.

Sirve para responder dos preguntas que hoy no se pueden contestar:
  - ¿Cuántas ofertas dejamos pasar por el límite del plan?
  - ¿Quién no llega al límite porque no hay ofertas, y quién porque topa?

Nada de lo que hay acá puede tumbar una postulación: todo va envuelto en
try/except y un fallo solo se imprime.
"""
import datetime

TABLA = "jobs-425301.DWH.METRICAS_DIARIAS"


def registrar(
    id_usuario: str,
    portal: str,
    plan: str,
    limite_dia: int,
    ofertas_compatibles: int,
    postuladas: int,
    no_postuladas_limite: int,
) -> bool:
    """
    Guarda (o reemplaza) la métrica del día para ese usuario y portal.
    Devuelve True si se escribió. Nunca lanza.
    """
    try:
        import bq
        from google.cloud import bigquery

        hoy = datetime.date.today().isoformat()
        cfg = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("uid", "STRING", id_usuario),
            bigquery.ScalarQueryParameter("portal", "STRING", portal),
            bigquery.ScalarQueryParameter("plan", "STRING", (plan or "FREE").upper()),
            bigquery.ScalarQueryParameter("limite", "INT64", int(limite_dia)),
            bigquery.ScalarQueryParameter("compatibles", "INT64", int(ofertas_compatibles)),
            bigquery.ScalarQueryParameter("postuladas", "INT64", int(postuladas)),
            bigquery.ScalarQueryParameter("perdidas", "INT64", int(no_postuladas_limite)),
        ])
        # MERGE para que una segunda corrida del mismo dia reemplace en vez de duplicar
        bq._query(f"""
            MERGE `{TABLA}` T
            USING (SELECT
                     DATE('{hoy}')            AS FECHA,
                     @uid                     AS ID_USUARIO,
                     @portal                  AS PORTAL,
                     @plan                    AS PLAN,
                     @limite                  AS LIMITE_DIA,
                     @compatibles             AS OFERTAS_COMPATIBLES,
                     @postuladas              AS POSTULADAS,
                     @perdidas                AS NO_POSTULADAS_LIMITE,
                     CURRENT_TIMESTAMP()      AS ACTUALIZADO
                  ) S
            ON  T.FECHA = S.FECHA
            AND T.ID_USUARIO = S.ID_USUARIO
            AND T.PORTAL = S.PORTAL
            WHEN MATCHED THEN UPDATE SET
              PLAN = S.PLAN, LIMITE_DIA = S.LIMITE_DIA,
              OFERTAS_COMPATIBLES = S.OFERTAS_COMPATIBLES,
              POSTULADAS = S.POSTULADAS,
              NO_POSTULADAS_LIMITE = S.NO_POSTULADAS_LIMITE,
              ACTUALIZADO = S.ACTUALIZADO
            WHEN NOT MATCHED THEN INSERT ROW
        """, cfg).result()
        return True
    except Exception as e:
        print(f"  [metricas] No se pudo registrar {id_usuario}/{portal}: {e}")
        return False


def resumen_hoy(id_usuario: str) -> dict:
    """Totales del día para un usuario, sumando todos los portales."""
    try:
        import bq
        from google.cloud import bigquery
        cfg = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("uid", "STRING", id_usuario),
        ])
        rows = list(bq._query(f"""
            SELECT
              IFNULL(SUM(OFERTAS_COMPATIBLES), 0)  AS compatibles,
              IFNULL(SUM(POSTULADAS), 0)           AS postuladas,
              IFNULL(SUM(NO_POSTULADAS_LIMITE), 0) AS perdidas,
              IFNULL(MAX(LIMITE_DIA), 0)           AS limite
            FROM `{TABLA}`
            WHERE FECHA = CURRENT_DATE('America/Santiago') AND ID_USUARIO = @uid
        """, cfg).result())
        return dict(rows[0]) if rows else {}
    except Exception as e:
        print(f"  [metricas] No se pudo leer resumen de {id_usuario}: {e}")
        return {}
