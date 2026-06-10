"""Envío de postulaciones por email usando la API de Resend."""

import os
import re
import json
import base64
import urllib.request
import urllib.error

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_DOMAIN     = os.environ.get("FROM_DOMAIN", "jobs.ko-deka.com")

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
    try:
        req = urllib.request.Request(cv_url, headers={
            "User-Agent": "Mozilla/5.0"
        })
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

    payload: dict = {
        "from":     f"{nombre} <postulaciones@{FROM_DOMAIN}>",
        "to":       [to_email],
        "reply_to": email,
        "subject":  subject,
        "html":     html,
    }

    # Adjuntar CV si existe
    cv_bytes = _download_cv(cv_url)
    if cv_bytes:
        safe_name = nombre.replace(" ", "_") if nombre else "CV"
        payload["attachments"] = [{
            "filename": f"CV_{safe_name}.pdf",
            "content":  base64.b64encode(cv_bytes).decode(),
        }]

    try:
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            "https://api.resend.com/emails",
            data=data,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type":  "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  ⚠ Resend HTTP {e.code}: {body[:200]}")
        return False
    except Exception as e:
        print(f"  ⚠ Error enviando email a {to_email}: {e}")
        return False
