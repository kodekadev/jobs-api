"""BigQuery client — lee usuarios activos y escribe empleos encontrados."""

import os
from google.cloud import bigquery

PROJECT = "jobs-425301"
DATASET = "DWH"
client  = bigquery.Client(project=PROJECT)

# Detectar la región del dataset probando get_dataset con el ID simple.
# Si falla, detectamos dinámicamente en la primera query que funcione.
def _detect_location() -> "str | None":
    # 1. Intentar con el dataset ID simple (sin project prefix)
    for ds_ref in [DATASET, f"{PROJECT}.{DATASET}"]:
        try:
            loc = client.get_dataset(ds_ref).location
            if loc:
                print(f"  [bq] Región detectada: {loc}")
                return loc
        except Exception:
            pass
    # 2. Probar con una query mínima en regiones comunes
    _TEST = f"SELECT 1 FROM `{PROJECT}.{DATASET}.CUENTAS_PORTALES` LIMIT 1"
    for loc in ["southamerica-east1", "southamerica-west1", "us-central1", "us-east1", None]:
        try:
            list(client.query(_TEST, location=loc).result())
            print(f"  [bq] Región detectada por prueba: {loc}")
            return loc
        except Exception:
            pass
    return None

_BQ_LOCATION: "str | None" = _detect_location()


def _query(sql: str, cfg: "bigquery.QueryJobConfig | None" = None):
    """Wrapper de client.query que siempre pasa la región correcta."""
    return client.query(sql, job_config=cfg, location=_BQ_LOCATION)


def _pf_select(alias: str = "pf") -> str:
    """Campos estándar de POSTULA_FACIL. Columnas en mayúsculas según esquema real."""
    a = alias
    return f"""
        {a}.ID_USUARIO,
        {a}.PROFESION,
        {a}.RESUMEN,
        {a}.CV_URL,
        {a}.CARGOS,
        {a}.UBICACIONES,
        {a}.PRETENSION_GENERAL,
        {a}.EXPERIENCIA,
        {a}.RUT,
        {a}.FECHA_NACIMIENTO,
        {a}.EMPRESA,
        {a}.ANIO_INICIO,
        {a}.ACTUALMENTE_TRABAJANDO,
        {a}.ANIO_FIN,
        {a}.NIVEL_EDUCATIVO,
        {a}.INSTITUCION,
        {a}.CARRERA,
        {a}.SITUACION_ESTUDIOS,
        {a}.ANIO_INICIO_ESTUDIOS,
        COALESCE(UPPER({a}.PLAN), 'FREE') AS plan
    """


def get_active_users() -> list[dict]:
    """Retorna usuarios con POSTULACIONES_AUTO activo=1 y POSTULA_FACIL completo."""
    query = f"""
        SELECT {_pf_select('pf')}, u.EMAIL
        FROM `{PROJECT}.{DATASET}.POSTULA_FACIL` pf
        INNER JOIN `{PROJECT}.{DATASET}.POSTULACIONES_AUTO` pa
            ON LOWER(pa.id_usuario) = LOWER(pf.ID_USUARIO)
        LEFT JOIN `{PROJECT}.{DATASET}.USUARIOS` u
            ON LOWER(u.ID_USUARIO) = LOWER(pf.ID_USUARIO)
        WHERE pa.activo = 1
          AND pf.CARGOS IS NOT NULL
          AND pf.UBICACIONES IS NOT NULL
          AND pf.PROFESION IS NOT NULL AND pf.PROFESION != ''
          AND pf.PRETENSION_GENERAL IS NOT NULL AND pf.PRETENSION_GENERAL != ''
          AND pf.RUT IS NOT NULL AND pf.RUT != ''
    """
    rows = list(_query(query).result())
    return [dict(r) for r in rows]


def get_user_by_id(user_id: str) -> list[dict]:
    """Retorna perfil completo de un usuario desde POSTULA_FACIL."""
    query = f"""
        SELECT {_pf_select('pf')}, u.EMAIL
        FROM `{PROJECT}.{DATASET}.POSTULA_FACIL` pf
        LEFT JOIN `{PROJECT}.{DATASET}.USUARIOS` u
            ON LOWER(u.ID_USUARIO) = LOWER(pf.ID_USUARIO)
        WHERE LOWER(pf.ID_USUARIO) = LOWER(@uid)
        LIMIT 1
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("uid", "STRING", user_id)]
    )
    return [dict(r) for r in _query(query, cfg).result()]


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
    return {r.id_empleo for r in _query(query, cfg).result()}


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
    return [dict(r) for r in _query(query, cfg).result()]


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
    rows = list(_query(query, cfg).result())
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
    _query(query, cfg).result()


def save_portal_cookies(user_id: str, portal: str, cookies: list[dict],
                        email: str = "", password: str = "") -> None:
    """Guarda cookies de sesión en CUENTAS_PORTALES. Crea la fila si no existe."""
    import json
    cookies_str = json.dumps(cookies)
    query = f"""
        MERGE `{PROJECT}.{DATASET}.CUENTAS_PORTALES` AS t
        USING (SELECT @uid AS id_usuario, @portal AS portal) AS s
        ON t.id_usuario = s.id_usuario AND t.portal = s.portal
        WHEN MATCHED THEN
            UPDATE SET cookies_json = @cookies, cookies_at = CURRENT_TIMESTAMP(),
                       updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN
            INSERT (id_usuario, portal, email, password, cookies_json, cookies_at, created_at, updated_at)
            VALUES (@uid, @portal, @email, @password, @cookies, CURRENT_TIMESTAMP(),
                    CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("uid",      "STRING", user_id),
        bigquery.ScalarQueryParameter("portal",   "STRING", portal),
        bigquery.ScalarQueryParameter("email",    "STRING", email),
        bigquery.ScalarQueryParameter("password", "STRING", password),
        bigquery.ScalarQueryParameter("cookies",  "STRING", cookies_str),
    ])
    _query(query, cfg).result()


def get_cookie_age_hours(user_id: str, portal: str) -> float | None:
    """Retorna cuántas horas han pasado desde que se guardaron las cookies, o None si no hay."""
    query = f"""
        SELECT TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), cookies_at, HOUR) AS age_hours
        FROM `{PROJECT}.{DATASET}.CUENTAS_PORTALES`
        WHERE id_usuario = @uid AND portal = @portal
          AND cookies_json IS NOT NULL
        LIMIT 1
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("uid",    "STRING", user_id),
        bigquery.ScalarQueryParameter("portal", "STRING", portal),
    ])
    rows = list(_query(query, cfg).result())
    if rows:
        return float(rows[0].age_hours)
    return None


def get_portal_cookies(user_id: str, portal: str, max_age_hours: int = 120) -> list[dict] | None:
    """Retorna cookies guardadas si existen y tienen menos de max_age_hours."""
    import json
    query = f"""
        SELECT cookies_json
        FROM `{PROJECT}.{DATASET}.CUENTAS_PORTALES`
        WHERE id_usuario = @uid AND portal = @portal
          AND cookies_json IS NOT NULL
          AND cookies_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {max_age_hours} HOUR)
        LIMIT 1
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("uid",    "STRING", user_id),
        bigquery.ScalarQueryParameter("portal", "STRING", portal),
    ])
    rows = list(_query(query, cfg).result())
    if rows and rows[0].cookies_json:
        return json.loads(rows[0].cookies_json)
    return None


def save_notificacion(user_id: str, titulo: str, empresa: str, link: str, portal: str) -> None:
    """Inserta una notificación de postulación para el usuario."""
    import uuid
    from datetime import datetime, timezone
    row = {
        "id":         str(uuid.uuid4()),
        "id_usuario": user_id,
        "titulo":     titulo[:500],
        "empresa":    empresa[:500],
        "link":       link[:1024],
        "portal":     portal[:100],
        "leida":      False,
        "fecha":      datetime.now(timezone.utc).isoformat(),
    }
    table = client.get_table(f"{PROJECT}.{DATASET}.NOTIFICACIONES")
    errors = client.insert_rows_json(table, [row])
    if errors:
        print(f"  ⚠ Notificacion insert error: {errors}")


def mark_notificaciones_leidas(user_id: str) -> None:
    """Marca todas las notificaciones del usuario como leídas."""
    query = f"""
        UPDATE `{PROJECT}.{DATASET}.NOTIFICACIONES`
        SET leida = TRUE
        WHERE id_usuario = @uid AND leida = FALSE
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("uid", "STRING", user_id)]
    )
    _query(query, cfg).result()




def get_notificaciones(user_id: str, solo_no_leidas: bool = False) -> list[dict]:
    """Retorna notificaciones del usuario, ordenadas por fecha desc."""
    filtro = "AND leida = FALSE" if solo_no_leidas else ""
    query = f"""
        SELECT id, titulo, empresa, link, portal, leida, fecha
        FROM `{PROJECT}.{DATASET}.NOTIFICACIONES`
        WHERE id_usuario = @uid {filtro}
        ORDER BY fecha DESC
        LIMIT 50
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("uid", "STRING", user_id)]
    )
    return [dict(r) for r in _query(query, cfg).result()]


_CV_OPT_LIMITS = {"FREE": 10, "PRO": 25, "PREMIUM": 50, "TRIAL": 25}


def contar_optimizaciones_hoy(user_id: str) -> int:
    """Cuántas optimizaciones LLM ha usado el usuario hoy."""
    query = f"""
        SELECT COUNT(*) AS total
        FROM `{PROJECT}.{DATASET}.CV_OPTIMIZACIONES`
        WHERE id_usuario = @uid AND fecha = CURRENT_DATE()
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("uid", "STRING", user_id)]
    )
    rows = list(_query(query, cfg).result())
    return int(rows[0].total) if rows else 0


def limite_optimizaciones(plan: str) -> int:
    """Retorna el límite diario de optimizaciones según el plan."""
    return _CV_OPT_LIMITS.get((plan or "FREE").upper(), _CV_OPT_LIMITS["FREE"])


def puede_optimizar(user_id: str, plan: str) -> bool:
    """True si el usuario no ha alcanzado su límite diario de optimizaciones."""
    return contar_optimizaciones_hoy(user_id) < limite_optimizaciones(plan)


def guardar_optimizacion(user_id: str, tipo: str = "respuesta_formulario",
                         id_empleo: str = "", portal: str = "",
                         tokens_usados: int = 0) -> None:
    """Registra una optimización LLM en CV_OPTIMIZACIONES."""
    import uuid as _uuid
    from datetime import datetime, timezone
    row = {
        "id":            str(_uuid.uuid4()),
        "id_usuario":    user_id,
        "fecha":         datetime.now(timezone.utc).date().isoformat(),
        "tipo":          tipo,
        "id_empleo":     id_empleo or None,
        "portal":        portal or None,
        "tokens_usados": tokens_usados or None,
        "created_at":    datetime.now(timezone.utc).isoformat(),
    }
    table = client.get_table(f"{PROJECT}.{DATASET}.CV_OPTIMIZACIONES")
    errors = client.insert_rows_json(table, [row])
    if errors:
        print(f"  [bq] CV_OPTIMIZACIONES insert error: {errors}")


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
            "portal":            (r.get("portal") or "")[:100],
        })
    if not valid:
        return
    table = client.get_table(f"{PROJECT}.{DATASET}.EMPLEOS")
    errors = client.insert_rows_json(table, valid)
    if errors:
        print(f"  ⚠ BigQuery insert errors: {errors}")
