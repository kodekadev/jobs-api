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


def _pf_select(alias: str = "pf", ic: str = None) -> str:
    """Campos estándar de POSTULA_FACIL. Columnas en mayúsculas según esquema real."""
    a = alias
    cv_url = (f"COALESCE(NULLIF({a}.CV_URL,''), NULLIF({ic}.CV_URL,'')) AS CV_URL" if ic
              else f"{a}.CV_URL")
    return f"""
        {a}.ID_USUARIO,
        {a}.PROFESION,
        {a}.RESUMEN,
        {cv_url},
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
        COALESCE(UPPER({a}.PLAN), 'FREE') AS plan,
        {a}.EMPRESAS_EXCLUIDAS,
        {a}.BUSQUEDA_ACTIVA,
        COALESCE(UPPER({a}.TIPO_BUSQUEDA), 'EMPLEO') AS TIPO_BUSQUEDA,
        COALESCE(UPPER({a}.JORNADA), 'FULL_TIME') AS JORNADA
    """


def get_users_postula_facil() -> list[dict]:
    """Retorna usuarios que llenaron PostulaFácil y tienen el autopilot activo (ACTIVO = 1).
    Usuarios sin fila en POSTULACIONES_AUTO se incluyen por compatibilidad con cuentas antiguas."""
    query = f"""
        SELECT {_pf_select('pf', 'ic')}, u.EMAIL, u.NOMBRE, u.CELULAR
        FROM `{PROJECT}.{DATASET}.POSTULA_FACIL` pf
        LEFT JOIN `{PROJECT}.{DATASET}.USUARIOS` u
            ON LOWER(u.ID_USUARIO) = LOWER(pf.ID_USUARIO)
        LEFT JOIN (
            SELECT ID_USUARIO, CV_URL
            FROM `{PROJECT}.{DATASET}.INFO_CLIENTE`
            QUALIFY ROW_NUMBER() OVER (PARTITION BY ID_USUARIO ORDER BY ID_USUARIO) = 1
        ) ic ON LOWER(ic.ID_USUARIO) = LOWER(pf.ID_USUARIO)
        LEFT JOIN `{PROJECT}.{DATASET}.POSTULACIONES_AUTO` pa
            ON LOWER(pa.id_usuario) = LOWER(pf.ID_USUARIO)
        WHERE pf.ID_USUARIO IS NOT NULL AND pf.ID_USUARIO != ''
          AND COALESCE(u.EMAIL, '') != ''
          AND pf.CV_URL IS NOT NULL AND pf.CV_URL != ''
          AND COALESCE(u.CELULAR, '') != ''
          AND COALESCE(pa.activo, 1) = 1
    """
    rows = list(_query(query).result())
    return [dict(r) for r in rows]


def get_users_sin_cv_completo() -> list[dict]:
    """Retorna usuarios que tienen cuenta en al menos un portal pero sin cv_completo = TRUE.
    Filtra en BQ para no traer usuarios que ya tienen el CV listo en todos sus portales."""
    query = f"""
        SELECT {_pf_select('pf', 'ic')}, u.EMAIL, u.NOMBRE, u.CELULAR
        FROM `{PROJECT}.{DATASET}.POSTULA_FACIL` pf
        LEFT JOIN `{PROJECT}.{DATASET}.USUARIOS` u
            ON LOWER(u.ID_USUARIO) = LOWER(pf.ID_USUARIO)
        LEFT JOIN `{PROJECT}.{DATASET}.INFO_CLIENTE` ic
            ON LOWER(ic.ID_USUARIO) = LOWER(pf.ID_USUARIO)
        WHERE pf.ID_USUARIO IS NOT NULL AND pf.ID_USUARIO != ''
          AND COALESCE(u.EMAIL, '') != ''
          AND EXISTS (
            SELECT 1 FROM `{PROJECT}.{DATASET}.CUENTAS_PORTALES` cp
            WHERE LOWER(cp.id_usuario) = LOWER(pf.ID_USUARIO)
              AND COALESCE(cp.cv_completo, FALSE) IS DISTINCT FROM TRUE
          )
    """
    rows = list(_query(query).result())
    return [dict(r) for r in rows]


def get_users_sin_cuentas() -> list[dict]:
    """Retorna usuarios de Postula Fácil que les falta cuenta en al menos un portal.
    Filtra en BQ para no traer usuarios que ya tienen todo creado."""
    query = f"""
        SELECT {_pf_select('pf', 'ic')}, u.EMAIL, u.NOMBRE, u.CELULAR
        FROM `{PROJECT}.{DATASET}.POSTULA_FACIL` pf
        LEFT JOIN `{PROJECT}.{DATASET}.USUARIOS` u
            ON LOWER(u.ID_USUARIO) = LOWER(pf.ID_USUARIO)
        LEFT JOIN `{PROJECT}.{DATASET}.INFO_CLIENTE` ic
            ON LOWER(ic.ID_USUARIO) = LOWER(pf.ID_USUARIO)
        LEFT JOIN `{PROJECT}.{DATASET}.CUENTAS_PORTALES` cp_tbj
            ON LOWER(cp_tbj.id_usuario) = LOWER(pf.ID_USUARIO) AND cp_tbj.portal = 'trabajando'
        LEFT JOIN `{PROJECT}.{DATASET}.CUENTAS_PORTALES` cp_cht
            ON LOWER(cp_cht.id_usuario) = LOWER(pf.ID_USUARIO) AND cp_cht.portal = 'chiletrabajos'
        WHERE pf.ID_USUARIO IS NOT NULL AND pf.ID_USUARIO != ''
          AND COALESCE(u.EMAIL, '') != ''
          AND (cp_tbj.id_usuario IS NULL OR cp_cht.id_usuario IS NULL)
    """
    rows = list(_query(query).result())
    return [dict(r) for r in rows]


def get_active_users() -> list[dict]:
    """Retorna usuarios con POSTULACIONES_AUTO activo=1 y POSTULA_FACIL completo.
    El plan se lee desde PLAN_CONTRATADO (fuente de verdad) aplicando la lógica
    de vigencia, no desde POSTULA_FACIL que puede estar desactualizado.
    """
    query = f"""
        SELECT * REPLACE(
          COALESCE(
            CASE
              WHEN pc_plan = 'FREE' THEN 'FREE'
              -- TRIAL: campo PLAN='TRIAL' (esquema nuevo) o ESTADO='TRIAL' (esquema legacy)
              WHEN (pc_plan = 'TRIAL' OR pc_estado = 'TRIAL')
                AND (
                  (pc_fecha_fin IS NOT NULL AND DATE(pc_fecha_fin) >= CURRENT_DATE())
                  OR (pc_fecha_fin IS NULL
                      AND DATE(pc_fecha_inicio) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
                )
                THEN 'TRIAL'
              WHEN pc_plan NOT IN ('FREE', 'TRIAL') AND pc_estado != 'TRIAL' AND (
                (pc_fecha_fin IS NOT NULL AND DATE(pc_fecha_fin) >= CURRENT_DATE())
                OR (pc_fecha_fin IS NULL
                    AND DATE(pc_fecha_inicio) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY))
              ) THEN pc_plan
              ELSE NULL
            END,
            'FREE'
          ) AS plan
        )
        FROM (
          SELECT {_pf_select('pf', 'ic')},
                 pc.PLAN        AS pc_plan,
                 pc.ESTADO      AS pc_estado,
                 pc.FECHA_INICIO AS pc_fecha_inicio,
                 pc.FECHA_FIN    AS pc_fecha_fin,
                 u.EMAIL,
                 u.NOMBRE,
                 u.CELULAR
          FROM `{PROJECT}.{DATASET}.POSTULA_FACIL` pf
          INNER JOIN `{PROJECT}.{DATASET}.POSTULACIONES_AUTO` pa
              ON LOWER(pa.id_usuario) = LOWER(pf.ID_USUARIO)
          LEFT JOIN `{PROJECT}.{DATASET}.USUARIOS` u
              ON LOWER(u.ID_USUARIO) = LOWER(pf.ID_USUARIO)
          LEFT JOIN `{PROJECT}.{DATASET}.INFO_CLIENTE` ic
              ON LOWER(ic.ID_USUARIO) = LOWER(pf.ID_USUARIO)
          LEFT JOIN (
            -- Un solo plan por usuario: el más reciente en estado activo/trial/cancelado-pendiente
            SELECT ID_USUARIO, PLAN, ESTADO, FECHA_INICIO, FECHA_FIN
            FROM `{PROJECT}.{DATASET}.PLAN_CONTRATADO`
            WHERE ESTADO IN ('ACTIVO', 'CANCELADO_PENDIENTE', 'TRIAL')
            QUALIFY ROW_NUMBER() OVER (
              PARTITION BY LOWER(ID_USUARIO) ORDER BY FECHA_INICIO DESC
            ) = 1
          ) pc ON LOWER(pc.ID_USUARIO) = LOWER(pf.ID_USUARIO)
          WHERE pa.activo = 1
            AND pf.CARGOS IS NOT NULL
            AND pf.UBICACIONES IS NOT NULL
            AND pf.PROFESION IS NOT NULL AND pf.PROFESION != ''
            AND pf.RUT IS NOT NULL AND pf.RUT != ''
            AND COALESCE(TRIM(u.CELULAR), '') != ''
            AND (
              COALESCE(UPPER(pf.TIPO_BUSQUEDA), 'EMPLEO') = 'PRACTICA'
              OR (pf.PRETENSION_GENERAL IS NOT NULL AND pf.PRETENSION_GENERAL != '')
            )
        )
    """
    rows = list(_query(query).result())
    result = []
    for r in rows:
        d = dict(r)
        d.pop('pc_plan', None)
        d.pop('pc_estado', None)
        d.pop('pc_fecha_inicio', None)
        d['fecha_fin'] = d.pop('pc_fecha_fin', None)
        result.append(d)
    return result


def get_user_by_id(user_id: str) -> list[dict]:
    """Retorna perfil completo de un usuario desde POSTULA_FACIL."""
    query = f"""
        SELECT {_pf_select('pf', 'ic')}, u.EMAIL, u.NOMBRE, u.CELULAR
        FROM `{PROJECT}.{DATASET}.POSTULA_FACIL` pf
        LEFT JOIN `{PROJECT}.{DATASET}.USUARIOS` u
            ON LOWER(u.ID_USUARIO) = LOWER(pf.ID_USUARIO)
        LEFT JOIN `{PROJECT}.{DATASET}.INFO_CLIENTE` ic
            ON LOWER(ic.ID_USUARIO) = LOWER(pf.ID_USUARIO)
        WHERE LOWER(pf.ID_USUARIO) = LOWER(@uid)
        LIMIT 1
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("uid", "STRING", user_id)]
    )
    return [dict(r) for r in _query(query, cfg).result()]


def get_applied_job_ids(user_id: str, days: int | None = None) -> set:
    """IDs de empleos ya registrados para este usuario (evitar duplicados).
    days: si se especifica, solo considera postulaciones de los últimos N días.
    """
    date_filter = (
        f"AND Fecha_Postulacion >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {int(days)} DAY)"
        if days else ""
    )
    query = f"""
        SELECT id_empleo
        FROM `{PROJECT}.{DATASET}.EMPLEOS`
        WHERE id_usuario = @uid {date_filter}
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
        WHERE UPPER(pc.PLAN) = 'TRIAL'
          AND CAST(pc.FECHA_FIN AS DATE) = DATE_ADD(CURRENT_DATE(), INTERVAL @days DAY)
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("days", "INT64", days)]
    )
    return [dict(r) for r in _query(query, cfg).result()]


def get_expired_trials_today() -> list[dict]:
    """Usuarios cuyo trial venció exactamente hoy."""
    query = f"""
        SELECT pc.ID_USUARIO, u.NOMBRE, u.EMAIL
        FROM `{PROJECT}.{DATASET}.PLAN_CONTRATADO` pc
        INNER JOIN `{PROJECT}.{DATASET}.USUARIOS` u
            ON LOWER(pc.ID_USUARIO) = LOWER(u.ID_USUARIO)
        WHERE UPPER(pc.PLAN) = 'TRIAL'
          AND CAST(pc.FECHA_FIN AS DATE) = CURRENT_DATE()
    """
    return [dict(r) for r in _query(query).result()]


def get_total_postulaciones(user_id: str) -> int:
    """Total de postulaciones históricas de un usuario (para segmentación trial)."""
    query = f"""
        SELECT COUNT(*) AS total
        FROM `{PROJECT}.{DATASET}.EMPLEOS`
        WHERE id_usuario = @uid
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("uid", "STRING", user_id)]
    )
    rows = list(_query(query, cfg).result())
    return rows[0].total if rows else 0


def perfil_hash(user: dict) -> str:
    """Hash MD5 de los campos del perfil que se sincronizan con los portales.
    Cambia solo cuando el usuario edita datos relevantes en Postula Fácil."""
    import hashlib, json as _json
    campos = {
        k: (user.get(k) or user.get(k.lower()) or "")
        for k in [
            "CV_URL", "RESUMEN", "EXPERIENCIA", "PROFESION",
            "PRETENSION_GENERAL", "NIVEL_EDUCATIVO", "CARRERA",
            "EMPRESA", "RUT", "FECHA_NACIMIENTO", "CELULAR",
        ]
    }
    for k in ["CARGOS", "UBICACIONES"]:
        v = user.get(k) or user.get(k.lower()) or []
        campos[k] = _json.dumps(v, ensure_ascii=False) if isinstance(v, list) else str(v)
    serialized = _json.dumps(campos, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(serialized.encode()).hexdigest()


def perfil_necesita_actualizar(user_id: str, portal: str, current_hash: str) -> bool:
    """True si el usuario nunca fue completado en este portal o sus datos cambiaron."""
    query = f"""
        SELECT perfil_hash
        FROM `{PROJECT}.{DATASET}.CUENTAS_PORTALES`
        WHERE id_usuario = @uid AND portal = @portal
        LIMIT 1
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("uid",    "STRING", user_id),
        bigquery.ScalarQueryParameter("portal", "STRING", portal),
    ])
    rows = list(_query(query, cfg).result())
    if not rows or not rows[0].perfil_hash:
        return True
    return rows[0].perfil_hash != current_hash


def save_perfil_completado(user_id: str, portal: str, hash_valor: str) -> None:
    """Marca el perfil como completado guardando el hash actual y cv_completo = TRUE."""
    query = f"""
        UPDATE `{PROJECT}.{DATASET}.CUENTAS_PORTALES`
        SET perfil_completado_at = CURRENT_TIMESTAMP(),
            perfil_hash = @hash,
            cv_completo = TRUE
        WHERE id_usuario = @uid AND portal = @portal
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("uid",    "STRING", user_id),
        bigquery.ScalarQueryParameter("portal", "STRING", portal),
        bigquery.ScalarQueryParameter("hash",   "STRING", hash_valor),
    ])
    _query(query, cfg).result()


def count_portal_emails_like(prefix: str) -> int:
    """Cuenta cuántas cuentas en CUENTAS_PORTALES tienen email que empieza con prefix."""
    from google.cloud import bigquery
    query = f"SELECT COUNT(*) AS cnt FROM `{PROJECT}.{DATASET}.CUENTAS_PORTALES` WHERE email LIKE @pattern"
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("pattern", "STRING", f"{prefix}%@gmail.com"),
    ])
    try:
        rows = list(_query(query, cfg).result())
        return int(rows[0].cnt) if rows else 0
    except Exception:
        return 0


def get_portal_account(user_id: str, portal: str) -> dict | None:
    """Retorna credenciales guardadas para un portal, o None si no existen."""
    query = f"""
        SELECT email, password,
               COALESCE(cv_completo, FALSE) AS cv_completo,
               email_verificado
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
        try:
            cv_ok = bool(r.cv_completo)
        except Exception:
            cv_ok = True
        # None = columna existe pero sin dato (cuenta antigua) → no bloquear
        ev = r.email_verificado  # True, False, o None
        return {"email": r.email, "password": r.password, "cv_completo": cv_ok,
                "email_verificado": ev}
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
                        email: str = "", password: str = "",
                        cv_completo: bool | None = None,
                        email_verificado: bool | None = None) -> None:
    """Guarda cookies de sesión en CUENTAS_PORTALES. Crea la fila si no existe."""
    import json
    cookies_str = json.dumps(cookies)
    cv_set = "" if cv_completo is None else ", cv_completo = @cv_completo"
    cv_ins_col = "" if cv_completo is None else ", cv_completo"
    cv_ins_val = "" if cv_completo is None else ", @cv_completo"
    ev_set = "" if email_verificado is None else ", email_verificado = @email_verificado"
    ev_ins_col = "" if email_verificado is None else ", email_verificado"
    ev_ins_val = "" if email_verificado is None else ", @email_verificado"
    query = f"""
        MERGE `{PROJECT}.{DATASET}.CUENTAS_PORTALES` AS t
        USING (SELECT @uid AS id_usuario, @portal AS portal) AS s
        ON t.id_usuario = s.id_usuario AND t.portal = s.portal
        WHEN MATCHED THEN
            UPDATE SET cookies_json = @cookies, cookies_at = CURRENT_TIMESTAMP(),
                       updated_at = CURRENT_TIMESTAMP(){cv_set}{ev_set}
        WHEN NOT MATCHED THEN
            INSERT (id_usuario, portal, email, password, cookies_json, cookies_at, created_at, updated_at{cv_ins_col}{ev_ins_col})
            VALUES (@uid, @portal, @email, @password, @cookies, CURRENT_TIMESTAMP(),
                    CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(){cv_ins_val}{ev_ins_val})
    """
    params = [
        bigquery.ScalarQueryParameter("uid",      "STRING", user_id),
        bigquery.ScalarQueryParameter("portal",   "STRING", portal),
        bigquery.ScalarQueryParameter("email",    "STRING", email),
        bigquery.ScalarQueryParameter("password", "STRING", password),
        bigquery.ScalarQueryParameter("cookies",  "STRING", cookies_str),
    ]
    if cv_completo is not None:
        params.append(bigquery.ScalarQueryParameter("cv_completo", "BOOL", cv_completo))
    if email_verificado is not None:
        params.append(bigquery.ScalarQueryParameter("email_verificado", "BOOL", email_verificado))
    _query(query, bigquery.QueryJobConfig(query_parameters=params)).result()


def update_portal_account(user_id: str, portal: str, cv_completo: bool | None = None,
                          email_verificado: bool | None = None,
                          password_invalida: bool | None = None) -> None:
    """Actualiza campos de CUENTAS_PORTALES sin tocar cookies."""
    sets, params = [], [
        bigquery.ScalarQueryParameter("uid",    "STRING", user_id),
        bigquery.ScalarQueryParameter("portal", "STRING", portal),
    ]
    if cv_completo is not None:
        sets.append("cv_completo = @cv_completo")
        params.append(bigquery.ScalarQueryParameter("cv_completo", "BOOL", cv_completo))
    if email_verificado is not None:
        sets.append("email_verificado = @email_verificado")
        params.append(bigquery.ScalarQueryParameter("email_verificado", "BOOL", email_verificado))
    if password_invalida is not None:
        sets.append("password_invalida = @password_invalida")
        params.append(bigquery.ScalarQueryParameter("password_invalida", "BOOL", password_invalida))
    if not sets:
        return
    query = f"""
        UPDATE `{PROJECT}.{DATASET}.CUENTAS_PORTALES`
        SET {", ".join(sets)}, updated_at = CURRENT_TIMESTAMP()
        WHERE id_usuario = @uid AND portal = @portal
    """
    _query(query, bigquery.QueryJobConfig(query_parameters=params)).result()


def get_cuentas_sin_verificar(portal: str, forzar: bool = False) -> list[dict]:
    """Retorna cuentas de un portal pendientes de verificación.
    forzar=True trae TODAS las cuentas del portal (útil para auditar que ninguna quedó atrás).
    """
    filtro_verificado = "" if forzar else "AND (cp.email_verificado IS NULL OR cp.email_verificado = FALSE)"
    query = f"""
        SELECT cp.id_usuario, cp.email, cp.password, cp.email_verificado, cp.password_invalida,
               u.NOMBRE AS nombre
        FROM `{PROJECT}.{DATASET}.CUENTAS_PORTALES` cp
        LEFT JOIN `{PROJECT}.{DATASET}.USUARIOS` u ON u.ID_USUARIO = cp.id_usuario
        WHERE cp.portal = @portal
          {filtro_verificado}
          AND cp.email IS NOT NULL AND cp.email != ''
        ORDER BY cp.created_at
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("portal", "STRING", portal),
    ])
    return [
        {"id_usuario": r.id_usuario, "email": r.email, "password": r.password,
         "email_verificado": r.email_verificado, "password_invalida": r.password_invalida,
         "nombre": r.nombre or ""}
        for r in _query(query, cfg).result()
    ]


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


def get_postulaciones_hoy(user_id: str) -> int:
    """Cuántas postulaciones hizo este usuario hoy (hora Chile, portales, no email directo)."""
    query = f"""
        SELECT COUNT(*) AS cnt
        FROM `{PROJECT}.{DATASET}.EMPLEOS`
        WHERE id_usuario = @uid
          AND DATE(Fecha_Postulacion, 'America/Santiago') = CURRENT_DATE('America/Santiago')
          AND portal NOT IN ('email_directo', '')
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("uid", "STRING", user_id),
    ])
    result = list(_query(query, cfg).result())
    return result[0].cnt


def ya_envio_email(user_id: str, email_reclutador: str) -> bool:
    """True si ya enviamos email a este reclutador para este usuario."""
    query = f"""
        SELECT COUNT(*) AS cnt
        FROM `{PROJECT}.{DATASET}.EMPLEOS`
        WHERE id_usuario = @uid
          AND descripcion LIKE @pat
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("uid", "STRING", user_id),
        bigquery.ScalarQueryParameter("pat", "STRING", f"%{email_reclutador}%"),
    ])
    result = list(_query(query, cfg).result())
    return result[0].cnt > 0


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


_EVAL_TABLE = f"`{PROJECT}.{DATASET}.EVALUACIONES_EMPLEO`"

_EVAL_DDL = f"""
CREATE TABLE IF NOT EXISTS `{PROJECT}.{DATASET}.EVALUACIONES_EMPLEO` (
    id_usuario       STRING    NOT NULL,
    link             STRING    NOT NULL,
    portal           STRING,
    titulo           STRING,
    aplica           BOOL      NOT NULL,
    razon            STRING,
    modelo           STRING,
    fecha_evaluacion TIMESTAMP NOT NULL
)
PARTITION BY DATE(fecha_evaluacion)
OPTIONS (require_partition_filter = false)
"""


def _ensure_eval_table():
    """Crea EVALUACIONES_EMPLEO si no existe. Se llama solo la primera vez."""
    try:
        _query(_EVAL_DDL).result()
    except Exception:
        pass


_eval_table_ready = False


def get_evaluacion_empleo(id_usuario: str, link: str, dias: int = 14) -> "dict | None":
    """Retorna evaluación LLM guardada para (usuario, link) en los últimos N días, o None."""
    try:
        cfg = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("uid",  "STRING", id_usuario),
            bigquery.ScalarQueryParameter("link", "STRING", link),
            bigquery.ScalarQueryParameter("dias", "INT64",  dias),
        ])
        rows = list(_query(
            f"""SELECT aplica, razon FROM {_EVAL_TABLE}
                WHERE id_usuario = @uid AND link = @link
                  AND fecha_evaluacion >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @dias DAY)
                ORDER BY fecha_evaluacion DESC LIMIT 1""",
            cfg,
        ).result())
        return dict(rows[0]) if rows else None
    except Exception:
        return None


def save_evaluacion_empleo(
    id_usuario: str, link: str, portal: str,
    titulo: str, aplica: bool, razon: str,
    modelo: str = "claude-haiku",
) -> None:
    """Guarda evaluación LLM de un empleo en caché BQ."""
    global _eval_table_ready
    import datetime
    row = {
        "id_usuario":       id_usuario,
        "link":             link[:1024],
        "portal":           (portal or "")[:50],
        "titulo":           (titulo or "")[:200],
        "aplica":           aplica,
        "razon":            (razon or "")[:500],
        "modelo":           modelo,
        "fecha_evaluacion": datetime.datetime.utcnow().isoformat(),
    }
    try:
        if not _eval_table_ready:
            _ensure_eval_table()
            _eval_table_ready = True
        table = client.get_table(f"{PROJECT}.{DATASET}.EVALUACIONES_EMPLEO")
        errors = client.insert_rows_json(table, [row])
        if errors:
            print(f"  [bq] eval cache insert errors: {errors}")
    except Exception:
        pass  # caché no crítica — no bloquear el flujo


def clear_evaluaciones_cache(solo_aprobadas: bool = True):
    """
    Borra evaluaciones LLM guardadas en caché.
    solo_aprobadas=True  → borra solo las que dijeron SÍ (posibles falsos positivos).
    solo_aprobadas=False → borra toda la caché.
    """
    where = "WHERE aplica = TRUE" if solo_aprobadas else ""
    try:
        job = _query(f"DELETE FROM {_EVAL_TABLE} {where}")
        job.result()
        print(f"[bq] Caché de evaluaciones limpiada ({'solo aprobadas' if solo_aprobadas else 'todas'})")
    except Exception as e:
        print(f"[bq] Error limpiando caché: {e}")


# ─── MODO REVISIÓN ────────────────────────────────────────────────────────────

def get_modo_revision(user_id: str) -> bool:
    """True si el usuario tiene modo_revision activado en POSTULACIONES_AUTO."""
    query = f"""
        SELECT COALESCE(modo_revision, FALSE) AS modo_revision
        FROM `{PROJECT}.{DATASET}.POSTULACIONES_AUTO`
        WHERE id_usuario = @uid LIMIT 1
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("uid", "STRING", user_id)]
    )
    try:
        rows = list(_query(query, cfg).result())
        return bool(rows[0].modo_revision) if rows else False
    except Exception:
        return False


def set_modo_revision(user_id: str, activo: bool) -> None:
    """Activa o desactiva el modo revisión para un usuario."""
    query = f"""
        UPDATE `{PROJECT}.{DATASET}.POSTULACIONES_AUTO`
        SET modo_revision = @activo
        WHERE id_usuario = @uid
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("uid",    "STRING", user_id),
        bigquery.ScalarQueryParameter("activo", "BOOL",   activo),
    ])
    _query(query, cfg).result()


def save_pending_jobs(user_id: str, portal: str, jobs: list[dict]) -> int:
    """Guarda empleos pendientes de revisión. Retorna cuántos se insertaron."""
    import uuid as _uuid
    from datetime import datetime, timezone, timedelta
    if not jobs:
        return 0
    now    = datetime.now(timezone.utc)
    expira = now + timedelta(hours=36)
    rows = []
    for j in jobs:
        rows.append({
            "id":               str(_uuid.uuid4()),
            "id_usuario":       user_id,
            "portal":           portal,
            "titulo":           (j.get("titulo") or "")[:500],
            "empresa":          (j.get("empresa") or "")[:500],
            "url":              (j.get("link") or j.get("url") or "")[:1024],
            "estado":           "pendiente",
            "fecha_encontrado": now.isoformat(),
            "fecha_expira":     expira.isoformat(),
            "fecha_accion":     None,
        })
    table = client.get_table(f"{PROJECT}.{DATASET}.EMPLEOS_PENDIENTES")
    errors = client.insert_rows_json(table, rows)
    if errors:
        print(f"  [bq] EMPLEOS_PENDIENTES insert errors: {errors}")
        return 0
    return len(rows)


def get_pending_jobs(user_id: str, solo_pendientes: bool = True) -> list[dict]:
    """Empleos en bandeja de revisión del usuario."""
    filtro = "AND estado = 'pendiente' AND fecha_expira > CURRENT_TIMESTAMP()" if solo_pendientes else ""
    query = f"""
        SELECT id, portal, titulo, empresa, url, estado, fecha_encontrado, fecha_expira
        FROM `{PROJECT}.{DATASET}.EMPLEOS_PENDIENTES`
        WHERE id_usuario = @uid {filtro}
        ORDER BY fecha_encontrado DESC
        LIMIT 200
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("uid", "STRING", user_id)]
    )
    rows = list(_query(query, cfg).result())
    return [
        {
            "id":               r.id,
            "portal":           r.portal,
            "titulo":           r.titulo,
            "empresa":          r.empresa,
            "url":              r.url,
            "estado":           r.estado,
            "fecha_encontrado": r.fecha_encontrado.isoformat() if hasattr(r.fecha_encontrado, "isoformat") else str(r.fecha_encontrado),
            "fecha_expira":     r.fecha_expira.isoformat() if hasattr(r.fecha_expira, "isoformat") else str(r.fecha_expira),
        }
        for r in rows
    ]


def get_approved_pending_jobs(user_id: str | None = None, portal: str | None = None) -> list[dict]:
    """Empleos aprobados listos para postular (no expirados)."""
    filtros = ["estado = 'aprobado'", "fecha_expira > CURRENT_TIMESTAMP()"]
    params: list = []
    if user_id:
        filtros.append("id_usuario = @uid")
        params.append(bigquery.ScalarQueryParameter("uid", "STRING", user_id))
    if portal:
        filtros.append("portal = @portal")
        params.append(bigquery.ScalarQueryParameter("portal", "STRING", portal))
    query = f"""
        SELECT id, id_usuario, portal, titulo, empresa, url
        FROM `{PROJECT}.{DATASET}.EMPLEOS_PENDIENTES`
        WHERE {" AND ".join(filtros)}
        ORDER BY fecha_encontrado
    """
    cfg = bigquery.QueryJobConfig(query_parameters=params) if params else None
    return [dict(r) for r in _query(query, cfg).result()]


def update_pending_job_estado(job_id: str, estado: str) -> None:
    """Cambia el estado de un empleo pendiente."""
    query = f"""
        UPDATE `{PROJECT}.{DATASET}.EMPLEOS_PENDIENTES`
        SET estado = @estado, fecha_accion = CURRENT_TIMESTAMP()
        WHERE id = @id
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("id",     "STRING", job_id),
        bigquery.ScalarQueryParameter("estado", "STRING", estado),
    ])
    _query(query, cfg).result()


def aprobar_todos_pending(user_id: str) -> int:
    """Aprueba todos los empleos pendientes no expirados del usuario. Retorna cuántos."""
    query = f"""
        UPDATE `{PROJECT}.{DATASET}.EMPLEOS_PENDIENTES`
        SET estado = 'aprobado', fecha_accion = CURRENT_TIMESTAMP()
        WHERE id_usuario = @uid
          AND estado = 'pendiente'
          AND fecha_expira > CURRENT_TIMESTAMP()
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("uid", "STRING", user_id)]
    )
    job = _query(query, cfg)
    job.result()
    return job.num_dml_affected_rows or 0
