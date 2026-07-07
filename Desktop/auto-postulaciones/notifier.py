"""Envía resumen diario consolidado al usuario (portales + email directo LinkedIn)."""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_DOMAIN    = os.environ.get("FROM_DOMAIN", "jobs.ko-deka.com")
APP_URL        = os.environ.get("APP_URL", "https://postulai.com")


def _get_email_directo_hoy(uid: str) -> list[dict]:
    """Consulta BigQuery para traer postulaciones de email directo de hoy para este usuario."""
    try:
        import bq
        from google.cloud.bigquery import QueryJobConfig, ScalarQueryParameter
        query = f"""
            SELECT titulo_empleo AS titulo, Empresa AS empresa
            FROM `{bq.PROJECT}.{bq.DATASET}.EMPLEOS`
            WHERE id_usuario = @uid
              AND DATE(Fecha_Postulacion, 'America/Santiago') = CURRENT_DATE('America/Santiago')
              AND STARTS_WITH(COALESCE(Descripcion, ''), '[email_directo]')
        """
        cfg  = QueryJobConfig(query_parameters=[ScalarQueryParameter("uid", "STRING", uid)])
        rows = bq.client.query(query, job_config=cfg).result()
        return [{"titulo": r.titulo or "", "empresa": r.empresa or ""} for r in rows]
    except Exception as e:
        print(f"  ! No se pudo traer email_directo desde BQ: {e}")
        return []


def _send_smtp(from_addr: str, to: str, subject: str, html: str) -> bool:
    """Envía email vía Resend SMTP (evita bloqueo Cloudflare en REST API)."""
    if not RESEND_API_KEY:
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["From"]    = from_addr
        msg["To"]      = to
        msg["Subject"] = subject
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP_SSL("smtp.resend.com", 465, timeout=15) as server:
            server.login("resend", RESEND_API_KEY)
            server.sendmail(from_addr, [to], msg.as_string())
        return True
    except Exception as e:
        raise e


def send_summary(user: dict, jobs_found: list[dict], applied: list[dict]) -> None:
    if not RESEND_API_KEY or not user.get("EMAIL"):
        return

    nombre = user.get("NOMBRE", "")
    to     = user.get("EMAIL")
    uid    = user.get("ID_USUARIO") or user.get("id") or ""

    # Postulaciones en portales (pasadas por parámetro)
    portales = applied or []

    # Postulaciones por email directo (LinkedIn scraper) — consulta BQ
    email_directo = _get_email_directo_hoy(uid) if uid else []

    total = len(portales) + len(email_directo)
    if total == 0:
        return

    def tabla(jobs: list[dict], limit: int = 15) -> str:
        if not jobs:
            return "<tr><td colspan='2' style='padding:10px;color:#94a3b8;font-style:italic'>Ninguna hoy</td></tr>"
        return "".join(
            f"<tr>"
            f"<td style='padding:8px 10px;border-bottom:1px solid #eee;font-weight:500'>{j.get('titulo','')[:60]}</td>"
            f"<td style='padding:8px 10px;border-bottom:1px solid #eee;color:#555'>{j.get('empresa','')[:40]}</td>"
            f"</tr>"
            for j in jobs[:limit]
        )

    def seccion(titulo: str, icono: str, jobs: list[dict]) -> str:
        count = len(jobs)
        color = "#2A8FA5" if count > 0 else "#94a3b8"
        return f"""
        <div style="background:white;border-radius:10px;padding:20px;border:1px solid #e2e8f0;margin-bottom:16px">
          <p style="margin:0 0 12px;font-weight:700;font-size:15px;color:{color}">
            {icono} {titulo} <span style="font-weight:400;color:#64748b;font-size:13px">({count})</span>
          </p>
          <table style="width:100%;border-collapse:collapse">
            <thead>
              <tr style="background:#f1f5f9">
                <th style="padding:8px 10px;text-align:left;font-size:12px;color:#64748b;font-weight:600">Cargo</th>
                <th style="padding:8px 10px;text-align:left;font-size:12px;color:#64748b;font-weight:600">Empresa</th>
              </tr>
            </thead>
            <tbody>{tabla(jobs)}</tbody>
          </table>
        </div>"""

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333">
      <div style="background:linear-gradient(135deg,#1E6E82,#2A8FA5);padding:32px;text-align:center;border-radius:12px 12px 0 0">
        <h1 style="color:white;margin:0;font-size:24px">¡Postulamos por ti hoy!</h1>
        <p style="color:rgba(255,255,255,.85);margin:10px 0 0;font-size:15px">
          Hola {nombre}, enviamos <strong style="color:white">{total} postulacion{'es' if total != 1 else ''}</strong> en tu nombre
        </p>
      </div>

      <div style="background:#f8fafc;padding:24px;border-radius:0 0 12px 12px;border:1px solid #e2e8f0">

        {seccion("Portales (Trabajando.cl / ChileTrabajos)", "🌐", portales)}
        {seccion("Email directo a empleadores (LinkedIn)", "📧", email_directo)}

        <div style="text-align:center;margin:24px 0">
          <a href="{APP_URL}/dashboard"
             style="background:#2A8FA5;color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:16px;display:inline-block">
            Ver todas mis postulaciones
          </a>
        </div>

        <p style="margin-top:20px;font-size:12px;color:#94a3b8;text-align:center">
          AplicAI · <a href="{APP_URL}" style="color:#2A8FA5">aplicai.cl</a>
        </p>
      </div>
    </div>
    """

    from_addr = f"AplicAI <postulaciones@{FROM_DOMAIN}>"
    subject   = f"✅ {total} postulacion{'es' if total != 1 else ''} enviadas hoy — AplicAI"
    try:
        _send_smtp(from_addr, to, subject, html)
        print(f"  ✓ Resumen consolidado enviado a {to} ({len(portales)} portales + {len(email_directo)} email directo)")
    except Exception as e:
        print(f"  ⚠ Error enviando resumen a {to}: {e}")


def send_trial_warning(user: dict, days_left: int) -> None:
    """Avisa al usuario que su prueba gratuita PRO está por vencer."""
    if not RESEND_API_KEY or not user.get("EMAIL"):
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
            <li>Hasta 50 empleos buscados por cargo</li>
            <li>3 ubicaciones por cargo</li>
            <li>25 postulaciones por email al día</li>
            <li>Resumen diario en tu correo</li>
          </ul>
        </div>

        <p style="margin-top:20px;font-size:12px;color:#94a3b8;text-align:center">
          Postulai · <a href="{APP_URL}" style="color:#2A8FA5">{APP_URL}</a>
        </p>
      </div>
    </div>
    """

    from_addr = f"AplicAI <hola@{FROM_DOMAIN}>"
    subject   = f"⏳ Tu prueba PRO vence en {days_left} días — AplicAI"
    try:
        _send_smtp(from_addr, to, subject, html)
        print(f"  ✓ Aviso trial enviado a {to}")
    except Exception as e:
        print(f"  ⚠ Error enviando aviso trial a {to}: {e}")
