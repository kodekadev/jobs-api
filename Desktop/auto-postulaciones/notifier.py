"""Envía resumen diario al usuario con los empleos encontrados/postulados."""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_DOMAIN    = os.environ.get("FROM_DOMAIN", "jobs.ko-deka.com")
APP_URL        = os.environ.get("APP_URL", "https://postulai.com")


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

    if not applied:
        return  # Solo notificar cuando realmente se postulo en nombre del usuario

    applied_count = len(applied)

    rows_applied = "".join(
        f"<tr>"
        f"<td style='padding:8px 10px;border-bottom:1px solid #eee;font-weight:500'>{j.get('titulo','')}</td>"
        f"<td style='padding:8px 10px;border-bottom:1px solid #eee;color:#555'>{j.get('empresa','')}</td>"
        f"</tr>"
        for j in applied[:15]
    )

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333">
      <div style="background:linear-gradient(135deg,#1E6E82,#2A8FA5);padding:32px;text-align:center;border-radius:12px 12px 0 0">
        <h1 style="color:white;margin:0;font-size:24px">¡Postulamos por ti hoy!</h1>
        <p style="color:rgba(255,255,255,.85);margin:10px 0 0;font-size:15px">
          Hola {nombre}, enviamos <strong style="color:white">{applied_count} postulacion{'es' if applied_count != 1 else ''}</strong> en tu nombre
        </p>
      </div>

      <div style="background:#f8fafc;padding:24px;border-radius:0 0 12px 12px;border:1px solid #e2e8f0">

        <div style="background:white;border-radius:10px;padding:20px;border:1px solid #e2e8f0;margin-bottom:20px">
          <p style="margin:0 0 14px;font-weight:700;font-size:15px;color:#1e293b">Cargos a los que postulamos</p>
          <table style="width:100%;border-collapse:collapse">
            <thead>
              <tr style="background:#f1f5f9">
                <th style="padding:8px 10px;text-align:left;font-size:12px;color:#64748b;font-weight:600">Cargo</th>
                <th style="padding:8px 10px;text-align:left;font-size:12px;color:#64748b;font-weight:600">Empresa</th>
              </tr>
            </thead>
            <tbody>{rows_applied}</tbody>
          </table>
        </div>

        <div style="text-align:center;margin:24px 0">
          <a href="{APP_URL}/mis-postulaciones"
             style="background:#2A8FA5;color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:16px;display:inline-block">
            Revisa tus postulaciones
          </a>
        </div>

        <p style="color:#64748b;font-size:14px;text-align:center;margin:0">
          Ve en detalle todo lo que postulamos por ti en la plataforma.
        </p>

        <p style="margin-top:20px;font-size:12px;color:#94a3b8;text-align:center">
          Postulai · <a href="{APP_URL}" style="color:#2A8FA5">{APP_URL}</a>
        </p>
      </div>
    </div>
    """

    from_addr = f"AplicAI <postulaciones@{FROM_DOMAIN}>"
    subject   = f"✅ Postulamos {applied_count} {'vez' if applied_count == 1 else 'veces'} por ti hoy — revisa tus postulaciones"
    try:
        _send_smtp(from_addr, to, subject, html)
        print(f"  ✓ Resumen enviado a {to}")
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
