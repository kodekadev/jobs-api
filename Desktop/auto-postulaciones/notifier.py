# -*- coding: utf-8 -*-
"""Envía resumen diario consolidado al usuario (portales + email directo LinkedIn)."""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

_NOTIFIER_DIR = r"C:\Users\bastian\Desktop\auto-postulaciones"

try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(os.path.join(_NOTIFIER_DIR, ".env"), override=True)
except Exception:
    pass

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_DOMAIN    = os.environ.get("FROM_DOMAIN", "aplicai.cl")
APP_URL        = os.environ.get("APP_URL", "https://aplicai.cl")
BCC_EMAIL      = os.environ.get("BCC_EMAIL", "")

# Cambiar a False para notificar postulaciones de hoy (comportamiento normal)
NOTIFICAR_AYER = False

def _fecha_sql() -> str:
    """Expresión SQL de la fecha a notificar (hoy o ayer según NOTIFICAR_AYER)."""
    if NOTIFICAR_AYER:
        return "DATE_SUB(CURRENT_DATE('America/Santiago'), INTERVAL 1 DAY)"
    return "CURRENT_DATE('America/Santiago')"

def _label_dia() -> str:
    return "ayer" if NOTIFICAR_AYER else "hoy"


def _tracking_pixel(uid: str, tipo: str) -> str:
    """Pixel 1x1 invisible para tracking de apertura."""
    if not uid:
        return ""
    url = f"{APP_URL}/api/track/open?uid={uid}&tipo={tipo}"
    return f'<img src="{url}" width="1" height="1" style="display:block;border:0" alt="" />'


def _tracked_link(uid: str, tipo: str, dest: str) -> str:
    """URL de destino con tracking de click."""
    if not uid:
        return f"{APP_URL}{dest}"
    from urllib.parse import quote
    return f"{APP_URL}/api/track/click?uid={uid}&tipo={tipo}&dest={quote(dest)}"


def _get_email_directo_hoy(uid: str) -> list[dict]:
    """Consulta BigQuery para traer postulaciones de email directo del día a notificar."""
    try:
        import bq
        from google.cloud.bigquery import QueryJobConfig, ScalarQueryParameter
        query = f"""
            SELECT titulo_empleo AS titulo, Empresa AS empresa
            FROM `{bq.PROJECT}.{bq.DATASET}.EMPLEOS`
            WHERE id_usuario = @uid
              AND DATE(Fecha_Postulacion, 'America/Santiago') = {_fecha_sql()}
              AND STARTS_WITH(COALESCE(Descripcion, ''), '[email_directo]')
        """
        cfg  = QueryJobConfig(query_parameters=[ScalarQueryParameter("uid", "STRING", uid)])
        rows = bq.client.query(query, job_config=cfg).result()
        return [{"titulo": r.titulo or "", "empresa": r.empresa or ""} for r in rows]
    except Exception as e:
        print(f"  ! No se pudo traer email_directo desde BQ: {e}")
        return []


def _get_portales_hoy(uid: str) -> list[dict]:
    """Postulaciones a portales del día a notificar (hoy o ayer según NOTIFICAR_AYER)."""
    try:
        import bq
        from google.cloud.bigquery import QueryJobConfig, ScalarQueryParameter
        query = f"""
            SELECT titulo_empleo AS titulo, Empresa AS empresa, portal
            FROM `{bq.PROJECT}.{bq.DATASET}.EMPLEOS`
            WHERE id_usuario = @uid
              AND DATE(Fecha_Postulacion, 'America/Santiago') = {_fecha_sql()}
              AND portal NOT IN ('email_directo', 'linkedin')
              AND (Descripcion IS NULL OR NOT STARTS_WITH(COALESCE(Descripcion, ''), '[email_directo]'))
        """
        cfg  = QueryJobConfig(query_parameters=[ScalarQueryParameter("uid", "STRING", uid)])
        rows = bq.client.query(query, job_config=cfg).result()
        return [{"titulo": r.titulo or "", "empresa": r.empresa or "", "portal": r.portal or ""} for r in rows]
    except Exception as e:
        print(f"  ! No se pudo traer portales desde BQ: {e}")
        return []


def _send_smtp(from_addr: str, to: str, subject: str, html: str) -> bool:
    """Envía email vía Resend SMTP (evita bloqueo Cloudflare en REST API)."""
    api_key = os.environ.get("RESEND_API_KEY", RESEND_API_KEY)
    bcc     = os.environ.get("BCC_EMAIL", BCC_EMAIL)
    if not api_key:
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["From"]    = from_addr
        msg["To"]      = to
        msg["Subject"] = subject
        if bcc:
            msg["Bcc"] = bcc
        msg.attach(MIMEText(html, "html", "utf-8"))
        recipients = [to] + ([bcc] if bcc else [])
        with smtplib.SMTP_SSL("smtp.resend.com", 465, timeout=15) as server:
            server.login("resend", api_key)
            server.sendmail(from_addr, recipients, msg.as_string())
        return True
    except Exception as e:
        raise e


def send_summary(user: dict, jobs_found: list[dict], applied: list[dict]) -> None:
    if not os.environ.get("RESEND_API_KEY", RESEND_API_KEY) or not user.get("EMAIL"):
        return

    nombre = user.get("NOMBRE", "")
    to     = user.get("EMAIL")
    uid    = user.get("ID_USUARIO") or user.get("id") or ""
    plan   = (user.get("plan") or user.get("PLAN") or "FREE").upper()

    # Si applied está vacío, consultamos BQ directamente (flujo Jenkins/postular_todos.py)
    portales      = applied or (_get_portales_hoy(uid) if uid else [])
    email_directo = _get_email_directo_hoy(uid) if uid else []

    total         = len(portales) + len(email_directo)
    if total == 0:
        return

    upsell_block = ""
    if plan in ("FREE", "TRIAL", "PRO"):
        planes_url  = _tracked_link(uid, "summary_planes", "/planes")
        limite_plan = {"FREE": 5, "TRIAL": 10, "PRO": 25}.get(plan, 10)
        pro_posts   = 25
        prem_posts  = 50
        if plan == "PRO":
            ganancia = f"Con <strong>PREMIUM</strong> habrías enviado <strong>{prem_posts}</strong>."
        else:
            ganancia = f"Con <strong>PRO</strong> habrías enviado <strong>{pro_posts}</strong>, con <strong>PREMIUM</strong> hasta <strong>{prem_posts}</strong>."
        upsell_block = f"""
        <div style="margin-top:28px;border-top:1px solid #E2E8F0;padding-top:24px;text-align:left">
          <p style="font-size:15px;color:#1E6E82;font-weight:700;margin:0 0 6px">
            Llegaste al límite de {limite_plan} postulaciones de hoy.
          </p>
          <p style="font-size:14px;color:#374151;margin:0 0 14px">
            {ganancia}
          </p>
          {_tabla_planes(plan)}
          <div style="text-align:center;margin-top:16px">
            <a href="{planes_url}"
               style="background:#7C3AED;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px;display:inline-block">
              Subir de plan &rarr;
            </a>
          </div>
        </div>"""

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333">
      <div style="background:linear-gradient(135deg,#1E6E82,#2A8FA5);padding:32px;text-align:center;border-radius:12px 12px 0 0">
        <h1 style="color:white;margin:0;font-size:24px">¡Postulamos por ti {_label_dia()}!</h1>
        <p style="color:rgba(255,255,255,.85);margin:10px 0 0;font-size:15px">
          Hola {nombre}, enviamos <strong style="color:white">{total} postulacion{'es' if total != 1 else ''}</strong> en tu nombre
        </p>
      </div>

      <div style="background:#f8fafc;padding:32px;border-radius:0 0 12px 12px;border:1px solid #e2e8f0;text-align:center">

        <div style="background:white;border-radius:12px;padding:24px;border:1px solid #e2e8f0;margin-bottom:24px">
          <p style="font-size:48px;font-weight:900;color:#1E6E82;margin:0">{total}</p>
          <p style="font-size:15px;color:#555;margin:8px 0 0">postulacion{'es enviadas' if total != 1 else ' enviada'} {_label_dia()}</p>
        </div>

        {upsell_block}

        <div style="margin-top:24px">
          <p style="color:#555;font-size:14px;margin:0 0 16px">
            Entrá a tu cuenta para ver el detalle completo de cada postulación.
          </p>
          <a href="{_tracked_link(uid, 'summary_postulaciones', '/mis-postulaciones')}"
             style="background:#2A8FA5;color:white;padding:14px 36px;border-radius:8px;text-decoration:none;font-weight:700;font-size:16px;display:inline-block">
            Ver mis postulaciones
          </a>
        </div>

        <p style="margin-top:28px;font-size:12px;color:#94a3b8">
          AplicAI · <a href="{APP_URL}" style="color:#2A8FA5">aplicai.cl</a>
        </p>
        {_tracking_pixel(uid, "summary")}
      </div>
    </div>
    """

    from_addr = f"AplicAI <postulaciones@{FROM_DOMAIN}>"
    subject   = f"{total} postulacion{'es' if total != 1 else ''} enviadas {_label_dia()} — AplicAI"
    try:
        _send_smtp(from_addr, to, subject, html)
        print(f"  OK: Resumen enviado a {to} ({total} postulaciones)")
    except Exception as e:
        print(f"  ERROR: Error enviando resumen a {to}: {e}")


def _tabla_planes(plan_actual: str = "TRIAL") -> str:
    """Tabla HTML comparativa: plan actual vs PRO vs PREMIUM."""
    plan_actual = (plan_actual or "FREE").upper()

    planes = {
        "FREE":    {"posts": "5/día",  "cargos": "1",  "cv_ia": "No",   "precio": "Gratis",         "color": "#94A3B8"},
        "TRIAL":   {"posts": "10/día", "cargos": "4",  "cv_ia": "1/mes","precio": "Gratis (7 días)", "color": "#854D0E"},
        "PRO":     {"posts": "25/día", "cargos": "4",  "cv_ia": "2/mes","precio": "$9.990/mes",      "color": "#1D4ED8"},
        "PREMIUM": {"posts": "50/día", "cargos": "10", "cv_ia": "5/mes","precio": "$19.990/mes",     "color": "#7C3AED"},
    }

    actual  = planes.get(plan_actual, planes["FREE"])
    pro     = planes["PRO"]
    premium = planes["PREMIUM"]

    label_actual = plan_actual if plan_actual != "FREE" else "GRATIS (ahora)"

    def th(txt, color, highlight=False):
        bg = "#EDE9FE" if highlight else "#F1F5F9"
        return f'<th style="text-align:center;padding:8px 10px;background:{bg};color:{color};font-weight:700;white-space:nowrap">{txt}</th>'

    def td(val, color, bold=False):
        fw = "700" if bold else "600"
        return f'<td style="padding:10px;text-align:center;color:{color};font-weight:{fw}">{val}</td>'

    rows = [
        ("Postulaciones/día", "posts"),
        ("Cargos de búsqueda", "cargos"),
        ("CV con IA",          "cv_ia"),
        ("Precio",             "precio"),
    ]

    # PRO ya está en el plan actual → solo mostrar actual vs PREMIUM
    show_pro_col = plan_actual not in ("PRO",)

    filas_html = ""
    for label, key in rows:
        border = "border-bottom:1px solid #E2E8F0;" if key != "precio" else ""
        pro_td = td(pro[key], pro["color"]) if show_pro_col else ""
        filas_html += f"""
        <tr style="{border}">
          <td style="padding:10px;color:#374151;font-weight:500;white-space:nowrap">{label}</td>
          {td(actual[key], actual["color"], bold=True)}
          {pro_td}
          {td(premium[key], premium["color"])}
        </tr>"""

    pro_th = th("PRO ✦", pro["color"]) if show_pro_col else ""

    return f"""
    <div style="overflow-x:auto;margin:16px 0">
    <table style="width:100%;border-collapse:collapse;font-size:13px;min-width:300px">
      <thead>
        <tr>
          <th style="text-align:left;padding:8px 10px;background:#F1F5F9;color:#64748B;font-weight:600"></th>
          {th(label_actual, actual["color"])}
          {pro_th}
          {th("PREMIUM +", premium["color"], highlight=True)}
        </tr>
      </thead>
      <tbody>{filas_html}
      </tbody>
    </table>
    </div>"""


def send_trial_conversion(user: dict, dias_restantes: int, n_postulaciones: int) -> None:
    """Email día −3 o día −1: avisa que el trial está por vencer (usuarios con valor)."""
    if not os.environ.get("RESEND_API_KEY", RESEND_API_KEY) or not user.get("EMAIL"):
        return

    nombre = user.get("NOMBRE") or user.get("nombre", "")
    to     = user.get("EMAIL")  or user.get("email", "")
    uid    = user.get("ID_USUARIO") or user.get("id") or ""

    if dias_restantes == 3:
        subject = f"{nombre}, ya postulamos a {n_postulaciones} empleos por ti — te quedan 3 días de prueba"
        col_antes, col_ahora = "AHORA (TRIAL)", "EN 3 DÍAS (GRATIS)"
        intro = (
            f"En estos días AplicAI trabajó por ti: optimizamos tu CV con IA y postulamos "
            f"automáticamente a <strong>{n_postulaciones} empleos</strong> que calzaban con tu perfil "
            f"— sin que movieras un dedo."
            f"<br><br>Tu prueba gratuita termina en 3 días. Cuando venza, tu búsqueda baja de golpe:"
        )
        cta_texto = "Seguir con PRO — $9.990/mes"
        cierre = "Si el servicio te está sirviendo, este es el momento de mantener el ritmo sin interrupciones."
    else:
        subject = f"Mañana tu búsqueda baja de 10 a 5 postulaciones — último día de prueba"
        col_antes, col_ahora = "HOY (TRIAL)", "MAÑANA (GRATIS)"
        intro = (
            f"Mañana termina tu prueba. Hasta hoy AplicAI hizo "
            f"<strong>{n_postulaciones} postulaciones</strong> por ti. "
            f"Al pasar a Gratis, esto cambia de inmediato:"
        )
        cta_texto = "Activar PRO antes de que termine — $9.990/mes"
        cierre = (
            "Si ya estás en conversaciones con empresas, no es el momento de bajar la intensidad. "
            "Si todavía no llegó la entrevista que buscas, mucho menos."
        )

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333">
      <div style="background:linear-gradient(135deg,#1E6E82,#2A8FA5);padding:32px;text-align:center;border-radius:12px 12px 0 0">
        <h1 style="color:white;margin:0;font-size:22px">⏳ Te quedan {dias_restantes} días a máxima potencia</h1>
        <p style="color:rgba(255,255,255,.8);margin:8px 0 0">Hola {nombre}</p>
      </div>

      <div style="background:#f8fafc;padding:28px;border-radius:0 0 12px 12px;border:1px solid #e2e8f0">
        <p style="line-height:1.7">{intro}</p>

        {_tabla_planes("TRIAL")}

        <p style="line-height:1.7">{cierre}</p>

        <div style="text-align:center;margin:24px 0">
          <a href="{_tracked_link(uid, 'trial_conversion', '/planes')}"
             style="background:#2A8FA5;color:white;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:700;font-size:15px;display:inline-block">
            {cta_texto}
          </a>
        </div>

        {"<p style='font-size:13px;color:#64748B;text-align:center'>¿Dudas sobre si PRO es para ti? Responde este correo y lo conversamos.</p>" if dias_restantes == 1 else ""}

        <p style="margin-top:20px;font-size:12px;color:#94a3b8;text-align:center">
          AplicAI · <a href="{APP_URL}" style="color:#2A8FA5">aplicai.cl</a>
        </p>
        {_tracking_pixel(uid, "trial_conversion")}
      </div>
    </div>
    """

    from_addr = f"AplicAI <hola@{FROM_DOMAIN}>"
    try:
        _send_smtp(from_addr, to, subject, html)
        print(f"  OK: Email trial (día -{dias_restantes}) enviado a {to}")
    except Exception as e:
        print(f"  ERROR: Error enviando email trial (día -{dias_restantes}) a {to}: {e}")


def send_trial_expired(user: dict, n_postulaciones: int) -> None:
    """Email día 0: el trial expiró, usuario recibió valor (N≥5). Secuencia de conversión completa."""
    if not os.environ.get("RESEND_API_KEY", RESEND_API_KEY) or not user.get("EMAIL"):
        return

    nombre = user.get("NOMBRE") or user.get("nombre", "")
    to     = user.get("EMAIL")  or user.get("email", "")
    uid    = user.get("ID_USUARIO") or user.get("id") or ""
    subject = "Tu búsqueda bajó a plan Gratis — reactívala hoy"

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333">
      <div style="background:linear-gradient(135deg,#DC2626,#EF4444);padding:32px;text-align:center;border-radius:12px 12px 0 0">
        <h1 style="color:white;margin:0;font-size:22px">Tu prueba terminó hoy</h1>
        <p style="color:rgba(255,255,255,.85);margin:8px 0 0">Hola {nombre}</p>
      </div>

      <div style="background:#f8fafc;padding:28px;border-radius:0 0 12px 12px;border:1px solid #e2e8f0">
        <p style="line-height:1.7">
          Tu prueba terminó hoy. Durante el trial AplicAI optimizó tu CV con IA y postuló
          a <strong>{n_postulaciones} empleos</strong> en tu nombre.
        </p>
        <p style="line-height:1.7">Esto es lo que cambió al pasar a Gratis:</p>

        {_tabla_planes("TRIAL")}

        <p style="line-height:1.7">
          AplicAI sigue funcionando, pero con mucho menos alcance y sin optimización de CV.
          Para volver al nivel que tenías:
        </p>

        <div style="text-align:center;margin:24px 0">
          <a href="{_tracked_link(uid, 'trial_expired', '/planes')}"
             style="background:#DC2626;color:white;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:700;font-size:15px;display:inline-block">
            Reactivar PRO — $9.990/mes
          </a>
        </div>

        <p style="font-size:13px;color:#64748B;text-align:center">
          ¿Prefieres ir más rápido? TURBO te da 40 posts/día por $14.990
          (pago único, 30 días sin renovación automática).
          <a href="{_tracked_link(uid, 'trial_expired', '/planes')}" style="color:#2A8FA5">Ver planes</a>
        </p>

        <p style="margin-top:20px;font-size:12px;color:#94a3b8;text-align:center">
          AplicAI · <a href="{APP_URL}" style="color:#2A8FA5">aplicai.cl</a>
        </p>
        {_tracking_pixel(uid, "trial_expired")}
      </div>
    </div>
    """

    from_addr = f"AplicAI <hola@{FROM_DOMAIN}>"
    try:
        _send_smtp(from_addr, to, subject, html)
        print(f"  OK: Email trial expirado enviado a {to}")
    except Exception as e:
        print(f"  ERROR: Error enviando email trial expirado a {to}: {e}")


def send_trial_expired_low_usage(user: dict) -> None:
    """Email día 0: el trial expiró, usuario no recibió valor visible (N<5). Tono de reactivación."""
    if not os.environ.get("RESEND_API_KEY", RESEND_API_KEY) or not user.get("EMAIL"):
        return

    nombre = user.get("NOMBRE") or user.get("nombre", "")
    to     = user.get("EMAIL")  or user.get("email", "")
    subject = "Tu prueba terminó — reactívala y lo ajustamos juntos"

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333">
      <div style="background:linear-gradient(135deg,#1E6E82,#2A8FA5);padding:32px;text-align:center;border-radius:12px 12px 0 0">
        <h1 style="color:white;margin:0;font-size:22px">Tu prueba PRO terminó</h1>
        <p style="color:rgba(255,255,255,.8);margin:8px 0 0">Hola {nombre}</p>
      </div>

      <div style="background:#f8fafc;padding:28px;border-radius:0 0 12px 12px;border:1px solid #e2e8f0">
        <p style="line-height:1.7">
          Tu prueba gratuita terminó hoy y tu búsqueda volvió a plan Gratis:
          5 postulaciones/día, 1 cargo y sin optimización de CV con IA.
        </p>
        <p style="line-height:1.7">
          Si la búsqueda no generó los resultados que esperabas, en muchos casos
          es cuestión de ajustar los cargos o las ubicaciones. Reactiva PRO y
          revisamos tu configuración juntos — sin costo adicional.
        </p>

        <div style="text-align:center;margin:24px 0">
          <a href="{APP_URL}/planes"
             style="background:#2A8FA5;color:white;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:700;font-size:15px;display:inline-block">
            Reactivar PRO — $9.990/mes
          </a>
        </div>


        <p style="margin-top:20px;font-size:12px;color:#94a3b8;text-align:center">
          AplicAI · <a href="{APP_URL}" style="color:#2A8FA5">aplicai.cl</a>
        </p>
      </div>
    </div>
    """

    from_addr = f"AplicAI <hola@{FROM_DOMAIN}>"
    try:
        _send_smtp(from_addr, to, subject, html)
        print(f"  OK: Email trial expirado (low usage) enviado a {to}")
    except Exception as e:
        print(f"  ERROR: Error enviando email trial expirado (low usage) a {to}: {e}")


def send_trial_daily_limit(user: dict, n_hoy: int) -> None:
    """Email cuando un usuario TRIAL alcanza las 10 postulaciones del día."""
    if not os.environ.get("RESEND_API_KEY", RESEND_API_KEY) or not user.get("EMAIL"):
        return

    nombre = user.get("NOMBRE") or user.get("nombre", "")
    to     = user.get("EMAIL")  or user.get("email", "")

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333">
      <div style="background:linear-gradient(135deg,#1E6E82,#2A8FA5);padding:32px;text-align:center;border-radius:12px 12px 0 0">
        <h1 style="color:white;margin:0;font-size:22px">🚀 Ya postulamos a {n_hoy} empleos por ti hoy</h1>
        <p style="color:rgba(255,255,255,.8);margin:8px 0 0">Hola {nombre}</p>
      </div>

      <div style="background:#f8fafc;padding:28px;border-radius:0 0 12px 12px;border:1px solid #e2e8f0">
        <p style="line-height:1.7">
          Alcanzaste el límite diario de tu prueba gratuita. Mañana volvemos a postular
          10 empleos por ti — así hasta que terminen tus 7 días de prueba.
        </p>
        <p style="line-height:1.7">
          <strong>¿Quieres que sigamos postulando hoy y todos los días sin límite?</strong><br>
          Con el plan PRO llegamos a <strong>25 postulaciones/día</strong> — 2.5× más oportunidades.
        </p>

        {_tabla_planes("TRIAL")}

        <div style="text-align:center;margin:24px 0">
          <a href="{APP_URL}/planes"
             style="background:#2A8FA5;color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:16px;display:inline-block">
            Contratar PRO — $9.990/mes &rarr;
          </a>
        </div>

        <p style="margin-top:20px;font-size:12px;color:#94a3b8;text-align:center">
          AplicAI · <a href="{APP_URL}" style="color:#2A8FA5">aplicai.cl</a>
        </p>
      </div>
    </div>
    """

    from_addr = f"AplicAI <hola@{FROM_DOMAIN}>"
    subject   = f"🚀 Ya postulamos a {n_hoy} empleos por ti hoy — ¿seguimos?"
    try:
        _send_smtp(from_addr, to, subject, html)
        print(f"  OK: Email limite diario trial enviado a {to}")
    except Exception as e:
        print(f"  ERROR: Email limite trial a {to}: {e}")


def send_trial_warning(user: dict, days_left: int) -> None:
    """Avisa al usuario que su prueba gratuita PRO está por vencer."""
    if not os.environ.get("RESEND_API_KEY", RESEND_API_KEY) or not user.get("EMAIL"):
        return

    nombre = user.get("NOMBRE", "")
    to     = user.get("EMAIL")

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333">
      <div style="background:linear-gradient(135deg,#1E6E82,#2A8FA5);padding:32px;text-align:center;border-radius:12px 12px 0 0">
        <h1 style="color:white;margin:0;font-size:22px">Tu prueba gratuita vence pronto</h1>
        <p style="color:rgba(255,255,255,.8);margin:8px 0 0">Hola {nombre}</p>
      </div>

      <div style="background:#f8fafc;padding:28px;border-radius:0 0 12px 12px;border:1px solid #e2e8f0">
        <div style="background:#FEF3C7;border:1px solid #FCD34D;border-radius:10px;padding:16px;margin-bottom:20px;text-align:center">
          <p style="margin:0;font-size:18px;font-weight:800;color:#92400E">⏳ {days_left} días restantes</p>
          <p style="margin:6px 0 0;color:#78350F;font-size:14px">Tu prueba PRO vence en {days_left} días</p>
        </div>

        <p>Con tu plan PRO estamos postulando automáticamente por ti a empleos en Chile todos los días hábiles.</p>

        <p>Para no perder esta funcionalidad, activa tu plan PRO antes de que venza la prueba:</p>

        <div style="text-align:center;margin:24px 0">
          <a href="{APP_URL}/dashboard"
             style="background:#2A8FA5;color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:16px">
            Activar plan PRO
          </a>
        </div>

        <div style="background:white;border-radius:10px;padding:16px;border:1px solid #e2e8f0;margin-top:16px">
          <p style="margin:0 0 8px;font-weight:700;font-size:14px">¿Qué incluye el plan PRO?</p>
          <ul style="margin:0;padding-left:20px;font-size:14px;color:#555;line-height:1.8">
            <li>Hasta 750 postulaciones automatizadas al mes</li>
            <li>4 cargos buscados — cobertura 5× mayor</li>
            <li>Resumen semanal de resultados</li>
            <li>Soporte prioritario</li>
          </ul>
        </div>

        <p style="margin-top:20px;font-size:12px;color:#94a3b8;text-align:center">
          AplicAI · <a href="{APP_URL}" style="color:#2A8FA5">aplicai.cl</a>
        </p>
      </div>
    </div>
    """

    from_addr = f"AplicAI <hola@{FROM_DOMAIN}>"
    subject   = f"⏳ Tu prueba PRO vence en {days_left} días — AplicAI"
    try:
        _send_smtp(from_addr, to, subject, html)
        print(f"  OK: Aviso trial enviado a {to}")
    except Exception as e:
        print(f"  ERROR: Error enviando aviso trial a {to}: {e}")


# ── Runner independiente (Stage 5 Jenkins) ────────────────────────────────────

def _run() -> None:
    """Envía resumen diario a todos los usuarios con postulaciones hoy, luego Telegram."""
    import os, sys
    _dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else r"C:\Users\bastian\Desktop\auto-postulaciones"
    if _dir not in sys.path:
        sys.path.insert(0, _dir)
    os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")

    from dotenv import load_dotenv
    load_dotenv(os.path.join(_dir, ".env"), override=True)

    import bq
    from datetime import date
    from telegram_notify import enviar as telegram

    from plan_limits import get_daily_limit

    api_key = os.environ.get("RESEND_API_KEY", "")
    print(f"[notifier] RESEND_API_KEY: {'OK (' + api_key[:8] + '...)' if api_key else 'FALTA — emails no se enviarán'}")
    print(f"[notifier] BCC: {os.environ.get('BCC_EMAIL', '(none)')}")

    all_users = bq.get_active_users()
    hoy = date.today().strftime("%d/%m")

    emails_ok   = 0
    emails_skip = 0
    total_posts = 0

    print(f"[notifier] Enviando resúmenes — {len(all_users)} usuarios activos")

    for user in all_users:
        uid    = user.get("ID_USUARIO") or user.get("id") or ""
        to     = user.get("EMAIL") or ""
        nombre = user.get("NOMBRE") or uid
        plan   = (user.get("plan") or user.get("PLAN") or "FREE").upper()
        limite = get_daily_limit(user)
        if not uid or not to:
            continue

        try:
            portales      = _get_portales_hoy(uid)
            email_directo = _get_email_directo_hoy(uid)
            total = len(portales) + len(email_directo)

            if total < limite:
                print(f"  [{uid}] {total}/{limite} postulaciones — aún no llegó al límite, skip")
                emails_skip += 1
                continue

            # Pasamos portales ya obtenidos para evitar doble consulta a BQ
            send_summary(user, [], portales)
            total_posts += total
            emails_ok   += 1
            print(f"  [{uid}] Email enviado ({total}/{limite} postulaciones)")
        except Exception as e:
            print(f"  [{uid}] Error: {e}")
            emails_skip += 1

    print(f"\n[notifier] Finalizado — {emails_ok} emails enviados, {emails_skip} sin postulaciones")

    msg = (
        f"[AplicAI] Notificaciones {hoy}\n"
        f"Emails enviados: {emails_ok}\n"
        f"Total postulaciones notificadas: {total_posts}\n"
        f"Usuarios sin postulaciones hoy: {emails_skip}"
    )
    telegram(msg)


if __name__ == "__main__":
    _run()
