"""BigQuery client — lee usuarios activos y escribe empleos encontrados."""

import os
from google.cloud import bigquery

PROJECT = "jobs-425301"
DATASET = "DWH"
client  = bigquery.Client(project=PROJECT)


def _plan_subquery() -> str:
    """LEFT JOIN con PLAN_CONTRATADO para obtener el plan activo del usuario."""
    return f"""
        LEFT JOIN (
            SELECT ID_USUARIO, PLAN
            FROM `{PROJECT}.{DATASET}.PLAN_CONTRATADO`
            WHERE UPPER(ESTADO) IN ('ACTIVO', 'TRIAL')
              AND DATE(FECHA_FIN) >= CURRENT_DATE()
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY ID_USUARIO
                ORDER BY CASE UPPER(ESTADO) WHEN 'ACTIVO' THEN 1 ELSE 2 END, FECHA_FIN DESC
            ) = 1
        ) pc ON LOWER(pc.ID_USUARIO) = LOWER(u.ID_USUARIO)
    """


def get_active_users() -> list[dict]:
    """Retorna usuarios con POSTULACIONES_AUTO activo=1, POSTULA_FACIL completo e incluye su plan."""
    query = f"""
        SELECT
            u.ID_USUARIO,
            u.NOMBRE,
            u.EMAIL,
            pf.cargos,
            pf.ubicaciones,
            pf.pretension_general,
            pf.cv_url,
            pf.profesion,
            pf.resumen,
            pf.experiencia,
            pf.pretension_general,
            pf.rut,
            pf.fecha_nacimiento,
            pf.empresa,
            pf.anio_inicio,
            pf.actualmente_trabajando,
            pf.anio_fin,
            pf.nivel_educativo,
            pf.institucion,
            pf.carrera,
            pf.situacion_estudios,
            pf.anio_inicio_estudios,
            COALESCE(UPPER(pc.PLAN), 'FREE') AS plan
        FROM `{PROJECT}.{DATASET}.USUARIOS` u
        INNER JOIN `{PROJECT}.{DATASET}.POSTULA_FACIL` pf
            ON LOWER(pf.id_usuario) = LOWER(u.ID_USUARIO)
        INNER JOIN `{PROJECT}.{DATASET}.POSTULACIONES_AUTO` pa
            ON LOWER(pa.id_usuario) = LOWER(u.ID_USUARIO)
        {_plan_subquery()}
        WHERE pa.activo = 1
          AND pf.cargos IS NOT NULL
          AND pf.ubicaciones IS NOT NULL
    """
    rows = list(client.query(query).result())
    return [dict(r) for r in rows]


def get_user_by_id(user_id: str) -> list[dict]:
    """Retorna un usuario específico aunque no tenga activo=1 (para trigger inmediato)."""
    query = f"""
        SELECT
            u.ID_USUARIO,
            u.NOMBRE,
            u.EMAIL,
            pf.cargos,
            pf.ubicaciones,
            pf.pretension_general,
            pf.cv_url,
            pf.profesion,
            pf.resumen,
            pf.experiencia,
            pf.pretension_general,
            pf.rut,
            pf.fecha_nacimiento,
            pf.empresa,
            pf.anio_inicio,
            pf.actualmente_trabajando,
            pf.anio_fin,
            pf.nivel_educativo,
            pf.institucion,
            pf.carrera,
            pf.situacion_estudios,
            pf.anio_inicio_estudios,
            COALESCE(UPPER(pc.PLAN), 'FREE') AS plan
        FROM `{PROJECT}.{DATASET}.USUARIOS` u
        INNER JOIN `{PROJECT}.{DATASET}.POSTULA_FACIL` pf
            ON LOWER(pf.id_usuario) = LOWER(u.ID_USUARIO)
        {_plan_subquery()}
        WHERE LOWER(u.ID_USUARIO) = LOWER(@uid)
        LIMIT 1
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("uid", "STRING", user_id)]
    )
    return [dict(r) for r in client.query(query, job_config=cfg).result()]


def get_applied_job_ids(user_id: str) -> set:
    """IDs de empleos ya registrados para este usuario (evitar duplicados)."""
    query = f"""
        SELECT id_empleo
        FROM `{PROJECT}.{DATASET}.EMPLEOS`
        WHERE id_usuario = @uid
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("uid", "STRING", user_id)]
    )
    return {r.id_empleo for r in client.query(query, job_config=cfg).result()}


def get_expiring_trials(days: int = 4) -> list[dict]:
    """Usuarios cuyo trial vence exactamente en 'days' días."""
    query = f"""
        SELECT pc.ID_USUARIO, u.NOMBRE, u.EMAIL
        FROM `{PROJECT}.{DATASET}.PLAN_CONTRATADO` pc
        INNER JOIN `{PROJECT}.{DATASET}.USUARIOS` u
            ON LOWER(pc.ID_USUARIO) = LOWER(u.ID_USUARIO)
        WHERE UPPER(pc.ESTADO) = 'TRIAL'
          AND CAST(pc.FECHA_FIN AS DATE) = DATE_ADD(CURRENT_DATE(), INTERVAL @days DAY)
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("days", "INT64", days)]
    )
    return [dict(r) for r in client.query(query, job_config=cfg).result()]


def get_portal_account(user_id: str, portal: str) -> dict | None:
    """Retorna credenciales guardadas para un portal, o None si no existen."""
    query = f"""
        SELECT email, password
        FROM `{PROJECT}.{DATASET}.CUENTAS_PORTALES`
        WHERE id_usuario = @uid AND portal = @portal
        LIMIT 1
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("uid",    "STRING", user_id),
        bigquery.ScalarQueryParameter("portal", "STRING", portal),
    ])
    rows = list(client.query(query, job_config=cfg).result())
    if rows:
        r = rows[0]
        return {"email": r.email, "password": r.password}
    return None


def save_portal_account(user_id: str, portal: str, email: str, password: str) -> None:
    """Guarda o actualiza credenciales de un portal para un usuario."""
    query = f"""
        MERGE `{PROJECT}.{DATASET}.CUENTAS_PORTALES` T
        USING (SELECT @uid AS id_usuario, @portal AS portal) S
        ON T.id_usuario = S.id_usuario AND T.portal = S.portal
        WHEN MATCHED THEN
            UPDATE SET email = @email, password = @password, updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN
            INSERT (id_usuario, portal, email, password, created_at, updated_at)
            VALUES (@uid, @portal, @email, @password, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("uid",      "STRING", user_id),
        bigquery.ScalarQueryParameter("portal",   "STRING", portal),
        bigquery.ScalarQueryParameter("email",    "STRING", email),
        bigquery.ScalarQueryParameter("password", "STRING", password),
    ])
    client.query(query, job_config=cfg).result()


def save_jobs(rows: list[dict]) -> None:
    """Inserta filas en EMPLEOS."""
    if not rows:
        return
    valid = []
    for r in rows:
        if not r.get("id_usuario"):
            print(f"  ⚠ save_jobs: fila sin id_usuario omitida — {r.get('titulo_empleo','?')[:60]}")
            continue
        valid.append({
            "id_empleo":         r.get("id_empleo", ""),
            "id_usuario":        r.get("id_usuario", ""),
            "titulo_empleo":     r.get("titulo_empleo", ""),
            "cargo":             r.get("cargo", ""),
            "Fecha_Postulacion": r.get("Fecha_Postulacion", ""),
            "empresa":           (r.get("empresa") or "")[:500],
            "descripcion":       (r.get("descripcion") or "")[:5000],
            "link":              (r.get("link") or "")[:1024],
        })
    if not valid:
        return
    table = client.get_table(f"{PROJECT}.{DATASET}.EMPLEOS")
    errors = client.insert_rows_json(table, valid)
    if errors:
        print(f"  ⚠ BigQuery insert errors: {errors}")
