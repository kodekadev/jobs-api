"""
Emails de retención / upsell para usuarios activos.

Flujo A — Sin ofertas (3+ días sin postulaciones):
  Usuarios con autopilot ON + plan vigente + cuentas en portales pero
  sin postulaciones en los últimos 3 días. Enganche para agregar más
  cargos o activar el TRIAL si no lo han usado.

Flujo B — FREE upsell:
  Usuarios en plan FREE con autopilot ON que ya están usando el sistema.
  Mostramos sus stats reales y comparamos con PRO/PREMIUM.

Cooldown: 14 días entre el mismo tipo de email (más conservador que onboarding).
"""
import os, sys, json

_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else r"C:\Users\bastian\Desktop\auto-postulaciones"
if _dir not in sys.path:
    sys.path.insert(0, _dir)

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")

from dotenv import load_dotenv
load_dotenv(os.path.join(_dir, ".env"))

import requests
import bq as _bq
from google.cloud import bigquery

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_DOMAIN    = os.environ.get("FROM_DOMAIN",    "aplicai.cl")
APP_URL        = os.environ.get("APP_URL",         "https://aplicai.cl")
BCC_EMAIL      = os.environ.get("BCC_EMAIL",       "bastian.alfaro@gmail.com")

TABLA_NOTIF    = f"`{_bq.PROJECT}.{_bq.DATASET}.NOTIF_ONBOARDING`"
COOLDOWN_DIAS  = 14
DIAS_SIN_POST  = 3   # días sin postulaciones para activar flujo A


# ── helpers ──────────────────────────────────────────────────────────────────

def _send(to: str, subject: str, html: str, debug: bool = False) -> bool:
    if not RESEND_API_KEY:
        print("  [notif] Sin RESEND_API_KEY — email omitido")
        return False
    try:
        html_safe = html.encode("ascii", errors="xmlcharrefreplace").decode("ascii")
        if debug:
            non_ascii = [c for c in html_safe if ord(c) > 127]
            print(f"  [debug] non-ASCII en html_safe: {non_ascii}")
            print(f"  [debug] primeros 300 chars: {repr(html_safe[:300])}")
        payload = {
            "from":    f"AplicAI <soporte@{FROM_DOMAIN}>",
            "to":      [to],
            "subject": subject,
            "html":    html_safe,
        }
        if BCC_EMAIL:
            payload["bcc"] = [BCC_EMAIL]
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json=payload,
            timeout=15,
        )
        if not resp.ok:
            print(f"  [notif] HTTP {resp.status_code} enviando a {to}: {resp.text}")
        return resp.ok
    except Exception as e:
        print(f"  [notif] Error enviando a {to}: {e}")
        return False


def _base(content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta http-equiv="Content-Type" content="text/html; charset=UTF-8"></head>
<body style="margin:0;padding:16px;background:#F8FAFC">
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;color:#333">
      <div style="padding:28px 0 20px">
        <span style="font-size:22px;font-weight:800;color:#1a1a2e">Aplic</span><span style="font-size:22px;font-weight:800;color:#2A8FA5">AI</span>
      </div>
      {content}
      <hr style="border:none;border-top:1px solid #E2E8F0;margin:32px 0"/>
      <p style="color:#94A3B8;font-size:12px;margin:0">
        AplicAI &middot; Santiago, Chile<br/>
        <a href="{APP_URL}" style="color:#2A8FA5">aplicai.cl</a> &middot;
        <a href="mailto:soporte@aplicai.cl" style="color:#2A8FA5">soporte@aplicai.cl</a>
      </p>
    </div>
</body></html>"""


def _btn(text: str, url: str, color: str = "#2A8FA5") -> str:
    return (
        f'<a href="{url}" style="display:inline-block;background:{color};color:white;'
        f'padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;'
        f'font-size:15px;margin-top:8px">{text}</a>'
    )


def _ya_enviado(uid: str, tipo: str) -> bool:
    try:
        rows = list(_bq.client.query(f"""
            SELECT 1 FROM {TABLA_NOTIF}
            WHERE ID_USUARIO = @uid AND TIPO = @tipo
              AND FECHA_ENVIO >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {COOLDOWN_DIAS} DAY)
            LIMIT 1
        """, job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("uid",  "STRING", uid),
            bigquery.ScalarQueryParameter("tipo", "STRING", tipo),
        ])).result())
        return len(rows) > 0
    except Exception:
        return False


def _registrar_envio(uid: str, tipo: str):
    try:
        _bq.client.query(f"""
            INSERT INTO {TABLA_NOTIF} (ID_USUARIO, TIPO, FECHA_ENVIO)
            VALUES (@uid, @tipo, CURRENT_TIMESTAMP())
        """, job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("uid",  "STRING", uid),
            bigquery.ScalarQueryParameter("tipo", "STRING", tipo),
        ])).result()
    except Exception as e:
        print(f"  [notif] No se pudo registrar {uid}/{tipo}: {e}")


# ── templates ─────────────────────────────────────────────────────────────────

def _e(s: str) -> str:
    """Convierte non-ASCII a entidades HTML numericas para garantizar body 100% ASCII."""
    return s.encode("ascii", errors="xmlcharrefreplace").decode("ascii")


def _html_sin_ofertas_free(nombre: str, cargos: list[str], ubicacion: str, tuvo_trial: bool) -> str:
    """FREE + 0 posts: el problema es que buscamos en 1 solo cargo. CTA = trial o upgrade."""
    nombre_s    = _e(nombre or "usuario")
    cargos_s    = _e(" &middot; ".join(cargos) if cargos else "tu cargo")
    ubicacion_s = _e(ubicacion or "tu region")

    if not tuvo_trial:
        cta_bloque = f"""
        <div style="background:#EFF6FF;border:1px solid #BFDBFE;border-radius:10px;padding:18px 20px;margin:20px 0">
          <p style="margin:0 0 6px;font-weight:700;color:#1D4ED8;font-size:15px">Prueba PRO gratis 7 d&iacute;as</p>
          <p style="margin:0 0 12px;color:#1E40AF;font-size:13px;line-height:1.5">
            Con el plan de prueba buscamos en <strong>4 cargos simult&aacute;neos</strong>
            y postulamos hasta 25 empleos por d&iacute;a &mdash; sin costo.
          </p>
          <a href="{APP_URL}/dashboard" style="display:inline-block;background:#1D4ED8;
             color:white;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px">
            Activar prueba gratuita &#8594;
          </a>
        </div>"""
    else:
        cta_bloque = f"""
        <div style="background:#F0FDF4;border:1px solid #86EFAC;border-radius:10px;padding:18px 20px;margin:20px 0">
          <p style="margin:0 0 6px;font-weight:700;color:#166534;font-size:15px">Ampl&iacute;a tu b&uacute;squeda con PRO</p>
          <p style="margin:0 0 12px;color:#15803D;font-size:13px;line-height:1.5">
            Con PRO ($9.990/mes) buscamos en <strong>4 cargos</strong> y postulamos
            25 empleos por d&iacute;a &mdash; 5&times; m&aacute;s cobertura.
          </p>
          <a href="{APP_URL}/planes" style="display:inline-block;background:#16A34A;
             color:white;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px">
            Ver planes &#8594;
          </a>
        </div>"""

    return _base(f"""
      <h2 style="color:#1a1a2e;margin:0 0 12px">Hola {nombre_s}</h2>
      <p style="color:#555;margin:0 0 16px;line-height:1.6">
        Llevamos {DIAS_SIN_POST} d&iacute;as buscando <strong>{cargos_s}</strong>
        en <strong>{ubicacion_s}</strong> pero no encontramos ofertas disponibles.
      </p>

      <div style="background:#FEF9C3;border:1px solid #FDE047;border-radius:10px;padding:14px 18px;margin:0 0 20px">
        <p style="margin:0 0 4px;font-weight:700;color:#713F12">Con el Plan Gratuito buscamos en 1 cargo</p>
        <p style="margin:0;color:#78350F;font-size:13px;line-height:1.5">
          Cuantos m&aacute;s cargos busquemos simult&aacute;neamente, m&aacute;s empleos encontramos.
          Con 1 cargo la cobertura es limitada &mdash; especialmente si el mercado est&aacute; con poca oferta.
        </p>
      </div>

      {cta_bloque}

      <p style="color:#888;font-size:13px;margin-top:8px">
        Tambi&eacute;n puedes ajustar tus cargos en
        <a href="{APP_URL}/postula-facil" style="color:#2A8FA5">tu perfil</a>
        para buscar t&eacute;rminos m&aacute;s amplios.
      </p>
    """)


def _html_sin_ofertas_pago(nombre: str, cargos: list[str], ubicacion: str) -> str:
    """Plan pago + 0 posts: mercado sin oferta para sus cargos. Sin upsell, solo ayuda operacional."""
    nombre_s    = _e(nombre or "usuario")
    cargos_s    = _e(" &middot; ".join(cargos) if cargos else "tus cargos")
    ubicacion_s = _e(ubicacion or "tu region")

    return _base(f"""
      <h2 style="color:#1a1a2e;margin:0 0 12px">Hola {nombre_s}</h2>
      <p style="color:#555;margin:0 0 16px;line-height:1.6">
        Llevamos {DIAS_SIN_POST} d&iacute;as revisando ofertas de
        <strong>{cargos_s}</strong> en <strong>{ubicacion_s}</strong>
        y no hemos encontrado nuevas publicaciones en los portales.
      </p>

      <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:14px 18px;margin:0 0 12px">
        <p style="margin:0 0 4px;font-weight:700;color:#1a1a2e">Revisar los t&eacute;rminos de b&uacute;squeda</p>
        <p style="margin:0;color:#555;font-size:13px;line-height:1.5">
          A veces un cargo se publica con nombres distintos (ej: &quot;Contador&quot; vs &quot;Analista Contable&quot;).
          Considera agregar variaciones en tu perfil.
        </p>
        <a href="{APP_URL}/postula-facil" style="display:inline-block;margin-top:8px;
           color:#2A8FA5;font-weight:700;font-size:13px;text-decoration:none">Editar perfil &#8594;</a>
      </div>

      <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:14px 18px;margin:0 0 20px">
        <p style="margin:0 0 4px;font-weight:700;color:#1a1a2e">Ampliar la ubicaci&oacute;n</p>
        <p style="margin:0;color:#555;font-size:13px;line-height:1.5">
          Si a&uacute;n no incluyes modalidad remota o regiones cercanas, puede haber
          m&aacute;s ofertas disponibles all&iacute;.
        </p>
        <a href="{APP_URL}/postula-facil" style="display:inline-block;margin-top:8px;
           color:#2A8FA5;font-weight:700;font-size:13px;text-decoration:none">Cambiar ubicaci&oacute;n &#8594;</a>
      </div>

      <p style="color:#888;font-size:13px">
        &iquest;Necesitas ayuda? Escr&iacute;benos a
        <a href="mailto:soporte@aplicai.cl" style="color:#2A8FA5">soporte@aplicai.cl</a>
      </p>
    """)


def _html_free_upsell(nombre: str, postulaciones_semana: int, postulaciones_total: int) -> str:
    nombre_s  = _e(nombre or "usuario")
    plural    = "s" if postulaciones_semana != 1 else ""
    total_fmt = f"{postulaciones_total:,}".replace(",", ".")

    return _base(f"""
      <h2 style="color:#1a1a2e;margin:0 0 12px">Hola {nombre_s}</h2>
      <p style="color:#555;margin:0 0 20px;line-height:1.6">
        Esta semana te postulamos a <strong>{postulaciones_semana} empleo{plural}</strong>
        &mdash; y en total llevamos <strong>{total_fmt}</strong> postulaciones desde que te registraste.
      </p>

      <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:12px;padding:20px;margin:0 0 20px">
        <p style="margin:0 0 14px;font-weight:700;color:#1a1a2e;font-size:15px">Con tu Plan Gratuito hoy:</p>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          <div style="flex:1;min-width:120px;background:white;border:1px solid #E2E8F0;border-radius:8px;padding:14px;text-align:center">
            <p style="margin:0;font-size:28px;font-weight:900;color:#64748B">5</p>
            <p style="margin:4px 0 0;font-size:12px;color:#94A3B8">postulaciones/d&iacute;a</p>
          </div>
          <div style="flex:1;min-width:120px;background:white;border:1px solid #E2E8F0;border-radius:8px;padding:14px;text-align:center">
            <p style="margin:0;font-size:28px;font-weight:900;color:#64748B">1</p>
            <p style="margin:4px 0 0;font-size:12px;color:#94A3B8">cargo buscado</p>
          </div>
          <div style="flex:1;min-width:120px;background:white;border:1px solid #E2E8F0;border-radius:8px;padding:14px;text-align:center">
            <p style="margin:0;font-size:28px;font-weight:900;color:#64748B">~150</p>
            <p style="margin:4px 0 0;font-size:12px;color:#94A3B8">postulaciones/mes</p>
          </div>
        </div>
      </div>

      <p style="color:#374151;font-weight:700;margin:0 0 12px">Imagina lo que conseguir&iacute;as con m&aacute;s cobertura:</p>

      <table style="width:100%;border-collapse:collapse;font-size:12px;margin:0 0 20px">
        <thead>
          <tr style="background:#F1F5F9">
            <th style="padding:8px 10px;text-align:left;color:#64748B;font-weight:600"></th>
            <th style="padding:8px 10px;text-align:center;color:#64748B;font-weight:600">Gratis</th>
            <th style="padding:8px 10px;text-align:center;color:#2A8FA5;font-weight:700">PRO<br/><span style="font-weight:400;font-size:11px">$9.990/mes</span></th>
            <th style="padding:8px 10px;text-align:center;color:#EA580C;font-weight:700">TURBO<br/><span style="font-weight:400;font-size:11px">$14.990 Unico</span></th>
            <th style="padding:8px 10px;text-align:center;color:#7C3AED;font-weight:700">PREMIUM<br/><span style="font-weight:400;font-size:11px">$19.990/mes</span></th>
          </tr>
        </thead>
        <tbody>
          <tr style="border-bottom:1px solid #E2E8F0">
            <td style="padding:8px 10px;color:#374151;font-weight:500">Posts/d&iacute;a</td>
            <td style="padding:8px 10px;text-align:center;color:#94A3B8">5</td>
            <td style="padding:8px 10px;text-align:center;color:#2A8FA5;font-weight:700">25</td>
            <td style="padding:8px 10px;text-align:center;color:#EA580C;font-weight:700">40</td>
            <td style="padding:8px 10px;text-align:center;color:#7C3AED;font-weight:700">50</td>
          </tr>
          <tr style="border-bottom:1px solid #E2E8F0">
            <td style="padding:8px 10px;color:#374151;font-weight:500">Cargos</td>
            <td style="padding:8px 10px;text-align:center;color:#94A3B8">1</td>
            <td style="padding:8px 10px;text-align:center;color:#2A8FA5;font-weight:700">4</td>
            <td style="padding:8px 10px;text-align:center;color:#EA580C;font-weight:700">6</td>
            <td style="padding:8px 10px;text-align:center;color:#7C3AED;font-weight:700">10</td>
          </tr>
          <tr style="border-bottom:1px solid #E2E8F0">
            <td style="padding:8px 10px;color:#374151;font-weight:500">~Posts/mes</td>
            <td style="padding:8px 10px;text-align:center;color:#94A3B8">150</td>
            <td style="padding:8px 10px;text-align:center;color:#2A8FA5;font-weight:700">750</td>
            <td style="padding:8px 10px;text-align:center;color:#EA580C;font-weight:700">1.200</td>
            <td style="padding:8px 10px;text-align:center;color:#7C3AED;font-weight:700">1.500</td>
          </tr>
          <tr>
            <td style="padding:8px 10px;color:#374151;font-weight:500">CV con IA</td>
            <td style="padding:8px 10px;text-align:center;color:#94A3B8">&mdash;</td>
            <td style="padding:8px 10px;text-align:center;color:#2A8FA5;font-weight:700">&#10003; 2/mes</td>
            <td style="padding:8px 10px;text-align:center;color:#EA580C;font-weight:700">&#10003; 3</td>
            <td style="padding:8px 10px;text-align:center;color:#7C3AED;font-weight:700">&#10003; 5/mes</td>
          </tr>
        </tbody>
      </table>

      <p style="color:#555;margin:0 0 20px;font-size:14px;line-height:1.6">
        Con PRO pasar&iacute;as de 5 a <strong>25 postulaciones diarias</strong>
        &mdash; 5&times; m&aacute;s chances de entrevista sin ning&uacute;n esfuerzo extra.
      </p>

      {_btn('Ver planes &#8594;', f'{APP_URL}/planes', '#2A8FA5')}

      <p style="color:#888;font-size:13px;margin-top:24px">
        &iquest;Tienes preguntas sobre los planes?
        <a href="mailto:soporte@aplicai.cl" style="color:#2A8FA5">soporte@aplicai.cl</a>
      </p>
    """)


# ── queries ───────────────────────────────────────────────────────────────────

def _get_sin_ofertas() -> list[dict]:
    """Usuarios con autopilot activo + cuentas en portales + 0 posts en los últimos DIAS_SIN_POST días."""
    query = f"""
    SELECT
      u.ID_USUARIO,
      u.NOMBRE,
      u.EMAIL,
      pf.CARGOS,
      pf.UBICACIONES,
      (pc.ID_USUARIO IS NULL) AS es_free,
      EXISTS(
        SELECT 1 FROM `{_bq.PROJECT}.{_bq.DATASET}.PLAN_CONTRATADO` pt
        WHERE pt.ID_USUARIO = u.ID_USUARIO AND UPPER(pt.ESTADO) = 'TRIAL'
      ) AS tuvo_trial
    FROM `{_bq.PROJECT}.{_bq.DATASET}.USUARIOS` u
    JOIN `{_bq.PROJECT}.{_bq.DATASET}.POSTULACIONES_AUTO` pa ON LOWER(u.ID_USUARIO) = LOWER(pa.id_usuario)
    JOIN `{_bq.PROJECT}.{_bq.DATASET}.POSTULA_FACIL` pf ON u.ID_USUARIO = pf.ID_USUARIO
    JOIN (
      SELECT DISTINCT LOWER(ID_USUARIO) AS uid
      FROM `{_bq.PROJECT}.{_bq.DATASET}.CUENTAS_PORTALES`
    ) cp ON LOWER(u.ID_USUARIO) = cp.uid
    LEFT JOIN (
      SELECT ID_USUARIO, COUNT(*) AS posts_recientes
      FROM `{_bq.PROJECT}.{_bq.DATASET}.EMPLEOS`
      WHERE FECHA_POSTULACION >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {DIAS_SIN_POST} DAY)
        AND portal NOT IN ('email_directo', '')
      GROUP BY ID_USUARIO
    ) e ON u.ID_USUARIO = e.ID_USUARIO
    -- plan vigente: FREE siempre vigente, planes pagos con FECHA_FIN futura
    LEFT JOIN (
      SELECT ID_USUARIO, PLAN, ESTADO, FECHA_FIN
      FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY ID_USUARIO ORDER BY FECHA_INICIO DESC) AS rn
        FROM `{_bq.PROJECT}.{_bq.DATASET}.PLAN_CONTRATADO`
        WHERE UPPER(ESTADO) IN ('ACTIVO', 'TRIAL') AND DATE(FECHA_FIN) >= CURRENT_DATE()
      ) WHERE rn = 1
    ) pc ON u.ID_USUARIO = pc.ID_USUARIO
    WHERE u.ACTIVO IS NOT FALSE
      AND u.EMAIL IS NOT NULL AND u.EMAIL != '' AND NOT STARTS_WITH(u.EMAIL, 'deleted_')
      AND pa.activo = 1
      AND COALESCE(e.posts_recientes, 0) = 0
      AND ARRAY_LENGTH(JSON_VALUE_ARRAY(pf.CARGOS)) > 0
    """
    rows = list(_bq.client.query(query).result())
    result = []
    for r in rows:
        try:
            cargos = json.loads(r.CARGOS) if r.CARGOS else []
        except Exception:
            cargos = [r.CARGOS] if r.CARGOS else []
        try:
            ubicaciones = json.loads(r.UBICACIONES) if r.UBICACIONES else []
        except Exception:
            ubicaciones = [r.UBICACIONES] if r.UBICACIONES else []
        ubicacion = (ubicaciones[0] if ubicaciones else "").split(",")[0].strip()
        result.append({
            "uid":        r.ID_USUARIO,
            "nombre":     r.NOMBRE or "",
            "email":      r.EMAIL or "",
            "cargos":     cargos,
            "ubicacion":  ubicacion,
            "es_free":    bool(r.es_free),
            "tuvo_trial": bool(r.tuvo_trial),
        })
    return result


def _get_free_upsell() -> list[dict]:
    """Usuarios FREE con autopilot activo y al menos 1 postulación esta semana."""
    query = f"""
    SELECT
      u.ID_USUARIO,
      u.NOMBRE,
      u.EMAIL,
      COALESCE(e7.posts_semana,  0) AS posts_semana,
      COALESCE(etotal.posts_total, 0) AS posts_total
    FROM `{_bq.PROJECT}.{_bq.DATASET}.USUARIOS` u
    JOIN `{_bq.PROJECT}.{_bq.DATASET}.POSTULACIONES_AUTO` pa ON LOWER(u.ID_USUARIO) = LOWER(pa.id_usuario)
    -- sin plan pago vigente = FREE
    LEFT JOIN (
      SELECT ID_USUARIO
      FROM `{_bq.PROJECT}.{_bq.DATASET}.PLAN_CONTRATADO`
      WHERE UPPER(ESTADO) IN ('ACTIVO', 'TRIAL') AND DATE(FECHA_FIN) >= CURRENT_DATE()
    ) pc ON u.ID_USUARIO = pc.ID_USUARIO
    LEFT JOIN (
      SELECT ID_USUARIO, COUNT(*) AS posts_semana
      FROM `{_bq.PROJECT}.{_bq.DATASET}.EMPLEOS`
      WHERE FECHA_POSTULACION >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
        AND portal NOT IN ('email_directo', '')
      GROUP BY ID_USUARIO
    ) e7 ON u.ID_USUARIO = e7.ID_USUARIO
    LEFT JOIN (
      SELECT ID_USUARIO, COUNT(*) AS posts_total
      FROM `{_bq.PROJECT}.{_bq.DATASET}.EMPLEOS`
      WHERE portal NOT IN ('email_directo', '')
      GROUP BY ID_USUARIO
    ) etotal ON u.ID_USUARIO = etotal.ID_USUARIO
    WHERE u.ACTIVO IS NOT FALSE
      AND u.EMAIL IS NOT NULL AND u.EMAIL != '' AND NOT STARTS_WITH(u.EMAIL, 'deleted_')
      AND pa.activo = 1
      AND pc.ID_USUARIO IS NULL          -- sin plan pago = FREE
      AND COALESCE(e7.posts_semana, 0) > 0  -- está usando el servicio
    """
    rows = list(_bq.client.query(query).result())
    return [{
        "uid":           r.ID_USUARIO,
        "nombre":        r.NOMBRE or "",
        "email":         r.EMAIL or "",
        "posts_semana":  int(r.posts_semana  or 0),
        "posts_total":   int(r.posts_total   or 0),
    } for r in rows]


# ── main ──────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False):
    print("[retention] Iniciando...")
    stats = {"sin_ofertas_free": 0, "sin_ofertas_pago": 0, "free_upsell": 0, "omitidos": 0, "errores": 0}

    # ── Flujo A: sin ofertas ──────────────────────────────────────────────────
    print(f"\n[A] Sin ofertas ({DIAS_SIN_POST} dias sin posts)...")
    for u in _get_sin_ofertas():
        tipo = "sin_ofertas_free" if u["es_free"] else "sin_ofertas_pago"
        if _ya_enviado(u["uid"], tipo):
            stats["omitidos"] += 1
            continue
        cargo_txt = u["cargos"][0] if u["cargos"] else "tu cargo"
        if u["es_free"]:
            subject = f"No hay ofertas para {cargo_txt} con 1 cargo buscado"
            html    = _html_sin_ofertas_free(u["nombre"], u["cargos"], u["ubicacion"], u["tuvo_trial"])
        else:
            subject = f"No encontramos ofertas para {cargo_txt} esta semana"
            html    = _html_sin_ofertas_pago(u["nombre"], u["cargos"], u["ubicacion"])
        if dry_run:
            plan_txt = "FREE" if u["es_free"] else "PAGO"
            trial_txt = f"trial={'si' if u['tuvo_trial'] else 'no'}" if u["es_free"] else ""
            print(f"  [DRY] {u['uid']} — {u['email']} | {plan_txt} {trial_txt} | {u['cargos']}")
            stats[tipo] += 1
            continue
        ok = _send(u["email"], subject, html)
        if ok:
            _registrar_envio(u["uid"], tipo)
            stats[tipo] += 1
            print(f"  ok [{u['uid']}] {tipo} -> {u['email']}")
        else:
            stats["errores"] += 1

    # ── Flujo B: FREE upsell ──────────────────────────────────────────────────
    print(f"\n[B] FREE upsell...")
    for u in _get_free_upsell():
        tipo = "free_upsell"
        if _ya_enviado(u["uid"], tipo):
            stats["omitidos"] += 1
            continue
        subject = f"Esta semana te postulamos {u['posts_semana']} empleos — multiplica tus chances"
        html    = _html_free_upsell(u["nombre"], u["posts_semana"], u["posts_total"])
        if dry_run:
            print(f"  [DRY] {u['uid']} — {u['email']} | semana: {u['posts_semana']} | total: {u['posts_total']}")
            stats["free_upsell"] += 1
            continue
        ok = _send(u["email"], subject, html)
        if ok:
            _registrar_envio(u["uid"], tipo)
            stats["free_upsell"] += 1
            print(f"  ✓ [{u['uid']}] {tipo} → {u['email']}")
        else:
            stats["errores"] += 1

    print(
        f"\n[retention] Fin:"
        f"\n  Sin ofertas FREE  -> {stats['sin_ofertas_free']}"
        f"\n  Sin ofertas pago  -> {stats['sin_ofertas_pago']}"
        f"\n  FREE upsell       -> {stats['free_upsell']}"
        f"\n  Omitidos(CD)      -> {stats['omitidos']}"
        f"\n  Errores           -> {stats['errores']}"
    )
    return stats


def send_test(to: str = "bastian.alfaro@gmail.com"):
    print(f"[test] Enviando 4 emails de retencion a {to}...")

    casos = [
        ("sin_ofertas_free_sin_trial",
         "[TEST A1] FREE sin trial - no hay ofertas",
         _html_sin_ofertas_free("Juan", ["Analista de Datos", "Data Analyst"], "Region Metropolitana", tuvo_trial=False)),
        ("sin_ofertas_free_con_trial",
         "[TEST A2] FREE con trial usado - no hay ofertas",
         _html_sin_ofertas_free("Maria", ["Disenador UX"], "Valparaiso", tuvo_trial=True)),
        ("sin_ofertas_pago",
         "[TEST A3] Plan pago - no hay ofertas",
         _html_sin_ofertas_pago("Roberto", ["Ingeniero Civil", "Jefe de Obra"], "Biobio")),
        ("free_upsell",
         "[TEST B] Esta semana te postulamos 32 empleos - multiplica tus chances",
         _html_free_upsell("Carlos", postulaciones_semana=32, postulaciones_total=187)),
    ]

    for tipo, subject, html in casos:
        ok = _send(to, subject, html)
        print(f"  {'ok' if ok else 'FALLO'} {tipo}")

    print("[test] Listo - revisa tu bandeja.")


if __name__ == "__main__":
    import sys
    # --dry-run imprime a quien se enviaria, sin enviar ni escribir en BQ
    _dry = "--dry-run" in sys.argv
    if _dry:
        print("[dry-run] simulacion: no se envia ningun correo")
    run(dry_run=_dry)
