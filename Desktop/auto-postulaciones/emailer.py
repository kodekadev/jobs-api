"""Envío de postulaciones por email usando Resend SMTP (evita bloqueo Cloudflare)."""

import os
import re
import smtplib
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_DOMAIN     = os.environ.get("FROM_DOMAIN", "aplicai.cl")

# Emails a ignorar en descripciones (no son de contacto)
_EXCLUDE = {"noreply", "no-reply", "donotreply", "linkedin", "indeed", "glassdoor",
            "computrabajo", "trabajando", "laborum", "bumeran", "getOnBoard"}


def extract_email(text: str) -> str | None:
    """Extrae el primer email de contacto de una descripción de empleo."""
    if not text:
        return None
    pattern = r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
    for m in re.findall(pattern, text):
        if not any(e in m.lower() for e in _EXCLUDE):
            return m
    return None


def _download_cv(cv_url: str) -> bytes | None:
    if not cv_url:
        return None
    # Usar GCS SDK con credenciales del SA cuando la URL es de Google Storage
    if "storage.googleapis.com" in cv_url:
        try:
            path = cv_url.split("storage.googleapis.com/", 1)[-1]
            bucket_name, blob_name = path.split("/", 1)
            from google.cloud import storage as gcs
            blob = gcs.Client().bucket(bucket_name).blob(blob_name)
            data = blob.download_as_bytes(timeout=30)
            return data
        except Exception as e:
            print(f"  ⚠ GCS SDK falló ({cv_url}): {e} — intentando HTTP")
    # Fallback HTTP para otras URLs
    try:
        req = urllib.request.Request(cv_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except Exception as e:
        print(f"  ⚠ No se pudo descargar CV ({cv_url}): {e}")
        return None


def send_application(user: dict, job: dict, to_email: str) -> bool:
    """
    Envía una postulación por email con CV adjunto.
    user: dict con NOMBRE, EMAIL, PROFESION, RESUMEN, cv_url
    job:  dict con titulo, empresa
    """
    if not RESEND_API_KEY:
        print("  ⚠ RESEND_API_KEY no configurada — email no enviado")
        return False

    nombre   = user.get("NOMBRE", "")
    email    = user.get("EMAIL", "")
    profesion = user.get("profesion") or user.get("PROFESION", "")
    resumen  = user.get("resumen") or user.get("RESUMEN", "")
    cv_url   = user.get("cv_url") or user.get("CV_URL", "")

    titulo_empleo = job.get("titulo", "empleo")
    empresa       = job.get("empresa", "la empresa")

    pretension = user.get("pretension_general") or user.get("PRETENSION_GENERAL", "")

    subject = f"Postulación: {titulo_empleo} — {nombre}"

    profesion_txt = f", {profesion}" if profesion else ""
    pretension_txt = (
        f'<p style="margin:0 0 12px">Mi pretensión de renta es <strong>{pretension}</strong>.</p>'
        if pretension else ""
    )
    resumen_parrafo = (
        f'<p style="margin:0 0 12px">{resumen}</p>'
        if resumen else ""
    )

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;color:#222;line-height:1.6">
      <p style="margin:0 0 12px">Estimado/a equipo de reclutamiento de <strong>{empresa}</strong>,</p>
      <p style="margin:0 0 12px">
        Me pongo en contacto para postular al cargo de <strong>{titulo_empleo}</strong>.
        Mi nombre es <strong>{nombre}</strong>{profesion_txt}.
      </p>
      {resumen_parrafo}
      {pretension_txt}
      <p style="margin:0 0 12px">
        Adjunto mi currículum vitae para su revisión. Quedo disponible para una entrevista
        cuando lo estimen conveniente.
      </p>
      <p style="margin:0">
        Saludos cordiales,<br>
        <strong>{nombre}</strong><br>
        <a href="mailto:{email}" style="color:#2A8FA5">{email}</a>
      </p>
    </div>
    """

    from_addr = f"{nombre} <postulaciones@{FROM_DOMAIN}>"
    cv_bytes  = _download_cv(cv_url)

    try:
        msg = MIMEMultipart("mixed")
        msg["From"]     = from_addr
        msg["To"]       = to_email
        msg["Subject"]  = subject
        msg["Reply-To"] = email
        msg.attach(MIMEText(html, "html", "utf-8"))

        if cv_bytes:
            safe_name = nombre.replace(" ", "_") if nombre else "CV"
            part = MIMEBase("application", "octet-stream")
            part.set_payload(cv_bytes)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="CV_{safe_name}.pdf"')
            msg.attach(part)

        with smtplib.SMTP_SSL("smtp.resend.com", 465, timeout=20) as server:
            server.login("resend", RESEND_API_KEY)
            server.sendmail(from_addr, [to_email], msg.as_string())
        return True
    except Exception as e:
        print(f"  ⚠ Error enviando email a {to_email}: {e}")
        return False
