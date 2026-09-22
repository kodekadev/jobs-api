"""
Flujos de recordatorio de onboarding para usuarios inactivos.

Segmentos:
  A) Sin postula-fácil  → llenar perfil para empezar
  B) Perfil incompleto  → completar cargos / ubicaciones / CV
  C) Listo, sin autopilot → activar el autopilot

Evita spam: no reenvía a alguien que ya recibió el mismo tipo en los últimos 7 días.
Ejecutar desde Spyder o cron diario.
"""
import os, sys

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
FROM_DOMAIN    = os.environ.get("FROM_DOMAIN", "aplicai.cl")
APP_URL        = os.environ.get("APP_URL", "https://aplicai.cl")
BCC_EMAIL      = os.environ.get("BCC_EMAIL", "bastian.alfaro@gmail.com")

TABLA_NOTIF    = f"`{_bq.PROJECT}.{_bq.DATASET}.NOTIF_ONBOARDING`"

# Días mínimos entre el mismo tipo de notificación
COOLDOWN_DIAS = 7
# Solo contactar usuarios con al menos N días de registro (no spamear el mismo día)
MIN_DIAS_REGISTRO = 1


# ── helpers ──────────────────────────────────────────────────────────────────

def _send(to: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        print("  [notif] Sin RESEND_API_KEY — email omitido")
        return False
    try:
        html_safe = html.encode("ascii", errors="xmlcharrefreplace").decode("ascii")
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


def _btn(text: str, url: str) -> str:
    return (
        f'<a href="{url}" style="display:inline-block;background:#2A8FA5;color:white;'
        f'padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;'
        f'font-size:15px;margin-top:8px">{text}</a>'
    )


# ── plantillas de email ───────────────────────────────────────────────────────

def _html_sin_perfil(nombre: str) -> str:
    return _base(f"""
      <h2 style="color:#1a1a2e;margin:0 0 12px">Hola {nombre or 'usuario'} 👋</h2>
      <p style="color:#555;margin:0 0 16px;line-height:1.6">
        Tienes cuenta en AplicAI pero todavía no configuraste tu perfil de búsqueda.
        <strong>Sin él, el autopilot no sabe qué empleos buscarte.</strong>
      </p>

      <div style="background:#F0F9FF;border-left:4px solid #2A8FA5;padding:16px 20px;border-radius:0 8px 8px 0;margin:0 0 20px">
        <p style="margin:0 0 10px;font-weight:700;color:#1a1a2e">Para activar el autopilot necesitas:</p>
        <p style="margin:4px 0;color:#555">📋 <strong>¿Qué cargo buscas?</strong> — Ej: Ingeniero de Datos, Analista Contable</p>
        <p style="margin:4px 0;color:#555">📍 <strong>¿Dónde quieres trabajar?</strong> — Región o ciudad</p>
        <p style="margin:4px 0;color:#555">📄 <strong>Tu CV</strong> — PDF actualizado</p>
      </div>

      <p style="color:#555;margin:0 0 20px;line-height:1.6">
        Solo toma 2 minutos. Una vez listo, postulamos automáticamente por ti todos los días.
      </p>
      {_btn('Completar mi perfil →', f'{APP_URL}/postula-facil')}
      <p style="color:#888;font-size:13px;margin-top:24px">
        ¿Dudas? Escríbenos a <a href="mailto:soporte@aplicai.cl" style="color:#2A8FA5">soporte@aplicai.cl</a>.
      </p>
    """)


_FALTA_DETALLE = {
    "Cargos que buscas":   ("📋", "¿Qué cargo estás buscando?", "Ej: Diseñador UX, Ingeniero Civil, Analista de Datos"),
    "Ubicación / región":  ("📍", "¿Dónde quieres trabajar?",   "Ej: Región Metropolitana, Valparaíso, Biobío"),
    "CV subido":           ("📄", "Sube tu CV en PDF",           "Tu hoja de vida actualizada para que la usen los portales"),
}

def _html_perfil_incompleto(nombre: str, falta: list[str]) -> str:
    items = ""
    for campo in falta:
        emoji, titulo, desc = _FALTA_DETALLE.get(campo, ("❓", campo, ""))
        items += f"""
        <div style="background:#FFFBEB;border:1px solid #FDE68A;border-radius:10px;padding:14px 16px;margin-bottom:10px">
          <p style="margin:0 0 4px;font-weight:700;color:#92400E">{emoji} {titulo}</p>
          <p style="margin:0;color:#78350F;font-size:13px">{desc}</p>
        </div>"""
    return _base(f"""
      <h2 style="color:#1a1a2e;margin:0 0 8px">Hola {nombre or 'usuario'}, falta poco 🚀</h2>
      <p style="color:#555;margin:0 0 20px;line-height:1.6">
        Empezaste a configurar tu perfil en AplicAI, pero nos falta esta información para activar el autopilot:
      </p>
      {items}
      <p style="color:#555;margin:20px 0;line-height:1.6">
        Completa estos datos y el sistema empieza a postular por ti automáticamente.
      </p>
      {_btn('Completar perfil →', f'{APP_URL}/postula-facil')}
      <p style="color:#888;font-size:13px;margin-top:24px">
        ¿Dudas? <a href="mailto:soporte@aplicai.cl" style="color:#2A8FA5">soporte@aplicai.cl</a>
      </p>
    """)


def _html_sin_autopilot(nombre: str) -> str:
    return _base(f"""
      <h2 style="color:#1a1a2e;margin:0 0 12px">¡Tu perfil está listo, {nombre or 'usuario'}! ✅</h2>
      <p style="color:#555;margin:0 0 16px;line-height:1.6">
        Ya tienes cargos, ubicación, CV y cuentas en los portales de empleo configurados.
        <strong>Solo falta activar el autopilot</strong> para que empecemos a trabajar por ti.
      </p>
      <div style="background:#F0FDF4;border:1px solid #86EFAC;border-radius:10px;padding:16px 20px;margin:0 0 20px">
        <p style="margin:0 0 8px;font-weight:700;color:#166534">Con el autopilot activo:</p>
        <p style="margin:4px 0;color:#15803D;font-size:14px">✓ Postulamos por ti todos los días automáticamente</p>
        <p style="margin:4px 0;color:#15803D;font-size:14px">✓ Buscamos en Trabajando, ChileTrabajos, Computrabajo y más</p>
        <p style="margin:4px 0;color:#15803D;font-size:14px">✓ Tú recibes notificaciones de cada postulación</p>
      </div>
      {_btn('Activar autopilot →', f'{APP_URL}/dashboard')}
      <p style="color:#888;font-size:13px;margin-top:24px">
        ¿Dudas? <a href="mailto:soporte@aplicai.cl" style="color:#2A8FA5">soporte@aplicai.cl</a>
      </p>
    """)


# ── lógica de BQ ──────────────────────────────────────────────────────────────

def _init_tabla():
    """Crea la tabla de tracking si no existe."""
    try:
        _bq.client.query(f"""
            CREATE TABLE IF NOT EXISTS {TABLA_NOTIF} (
              ID_USUARIO  STRING NOT NULL,
              TIPO        STRING NOT NULL,
              FECHA_ENVIO TIMESTAMP NOT NULL
            )
        """).result()
    except Exception as e:
        print(f"  [notif] No se pudo inicializar tabla tracking: {e}")


def _ya_enviado(uid: str, tipo: str) -> bool:
    """True si ya enviamos este tipo en los últimos COOLDOWN_DIAS días."""
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
        print(f"  [notif] No se pudo registrar envío {uid}/{tipo}: {e}")


def _get_segmentos() -> dict[str, list[dict]]:
    """
    Devuelve tres listas de usuarios por segmento.
    Sólo incluye usuarios activos con al menos MIN_DIAS_REGISTRO días registrados.
    """
    query = f"""
    SELECT
      u.ID_USUARIO,
      u.NOMBRE,
      u.EMAIL,
      (pf.ID_USUARIO IS NOT NULL)                                              AS tiene_pf,
      COALESCE(
        ARRAY_LENGTH(JSON_VALUE_ARRAY(pf.CARGOS)) > 0, FALSE
      )                                                                        AS tiene_cargos,
      COALESCE(
        ARRAY_LENGTH(JSON_VALUE_ARRAY(pf.UBICACIONES)) > 0, FALSE
      )                                                                        AS tiene_ubicaciones,
      COALESCE(pf.CV_URL IS NOT NULL AND pf.CV_URL != '', FALSE)               AS tiene_cv_pf,
      COALESCE(pa.activo, 0)                                                   AS autopilot_activo,
      COALESCE(
        MAX(cp.CV_COMPLETO), FALSE
      )                                                                        AS tiene_cv_portal,
      (u.CELULAR IS NOT NULL AND u.CELULAR != '')                              AS tiene_celular
    FROM `{_bq.PROJECT}.{_bq.DATASET}.USUARIOS` u
    LEFT JOIN `{_bq.PROJECT}.{_bq.DATASET}.POSTULA_FACIL`      pf ON u.ID_USUARIO = pf.ID_USUARIO
    LEFT JOIN `{_bq.PROJECT}.{_bq.DATASET}.POSTULACIONES_AUTO` pa ON LOWER(u.ID_USUARIO) = LOWER(pa.id_usuario)
    LEFT JOIN `{_bq.PROJECT}.{_bq.DATASET}.CUENTAS_PORTALES`   cp ON LOWER(u.ID_USUARIO) = LOWER(cp.ID_USUARIO)
    WHERE u.ACTIVO IS NOT FALSE
      AND u.EMAIL IS NOT NULL AND u.EMAIL != ''
      AND NOT STARTS_WITH(u.EMAIL, 'deleted_')
      AND DATE(u.FECHA_REGISTRO, 'America/Santiago') <= DATE_SUB(CURRENT_DATE('America/Santiago'), INTERVAL {MIN_DIAS_REGISTRO} DAY)
    GROUP BY u.ID_USUARIO, u.NOMBRE, u.EMAIL, pf.ID_USUARIO, pf.CARGOS, pf.UBICACIONES, pf.CV_URL, pa.activo, u.CELULAR
    """
    rows = list(_bq.client.query(query).result())

    sin_perfil, incompleto, sin_auto = [], [], []
    for r in rows:
        u = {
            "uid":   r.ID_USUARIO,
            "nombre": r.NOMBRE or "",
            "email":  r.EMAIL or "",
        }
        if not r.tiene_pf:
            sin_perfil.append(u)
        elif not r.tiene_cargos or not r.tiene_ubicaciones or not r.tiene_cv_pf or not r.tiene_celular:
            falta = []
            if not r.tiene_cargos:      falta.append("Cargos que buscas")
            if not r.tiene_ubicaciones: falta.append("Ubicación / región")
            if not r.tiene_cv_pf:       falta.append("CV subido")
            if not r.tiene_celular:     falta.append("Número de celular")
            u["falta"] = falta
            incompleto.append(u)
        elif not r.autopilot_activo:
            sin_auto.append(u)

    return {"sin_perfil": sin_perfil, "incompleto": incompleto, "sin_autopilot": sin_auto}


# ── main ──────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False):
    """
    dry_run=True imprime qué se enviaría sin enviar nada ni escribir en BQ.
    """
    print("[onboarding-notif] Iniciando...")
    _init_tabla()
    segmentos = _get_segmentos()

    stats = {"sin_perfil": 0, "incompleto": 0, "sin_autopilot": 0, "omitidos": 0, "errores": 0}

    # ── Segmento A: sin postula-fácil ────────────────────────────────────────
    for u in segmentos["sin_perfil"]:
        tipo = "sin_perfil"
        if _ya_enviado(u["uid"], tipo):
            stats["omitidos"] += 1
            continue
        subject = "Completa tu perfil y empieza a postular automáticamente"
        html    = _html_sin_perfil(u["nombre"])
        if dry_run:
            print(f"  [DRY] {u['uid']} — {u['email']} — {tipo}")
            stats["sin_perfil"] += 1
            continue
        ok = _send(u["email"], subject, html)
        if ok:
            _registrar_envio(u["uid"], tipo)
            stats["sin_perfil"] += 1
            print(f"  ✓ [{u['uid']}] {tipo} → {u['email']}")
        else:
            stats["errores"] += 1

    # ── Segmento B: perfil incompleto ────────────────────────────────────────
    for u in segmentos["incompleto"]:
        tipo = "perfil_incompleto"
        if _ya_enviado(u["uid"], tipo):
            stats["omitidos"] += 1
            continue
        subject = "Falta poco — completa tu perfil en AplicAI"
        html    = _html_perfil_incompleto(u["nombre"], u.get("falta", []))
        if dry_run:
            print(f"  [DRY] {u['uid']} — {u['email']} — {tipo} | falta: {u.get('falta')}")
            stats["incompleto"] += 1
            continue
        ok = _send(u["email"], subject, html)
        if ok:
            _registrar_envio(u["uid"], tipo)
            stats["incompleto"] += 1
            print(f"  ✓ [{u['uid']}] {tipo} → {u['email']}")
        else:
            stats["errores"] += 1

    # ── Segmento C: listo, sin autopilot ─────────────────────────────────────
    for u in segmentos["sin_autopilot"]:
        tipo = "sin_autopilot"
        if _ya_enviado(u["uid"], tipo):
            stats["omitidos"] += 1
            continue
        subject = "Tu perfil está listo — activa el autopilot"
        html    = _html_sin_autopilot(u["nombre"])
        if dry_run:
            print(f"  [DRY] {u['uid']} — {u['email']} — {tipo}")
            stats["sin_autopilot"] += 1
            continue
        ok = _send(u["email"], subject, html)
        if ok:
            _registrar_envio(u["uid"], tipo)
            stats["sin_autopilot"] += 1
            print(f"  ✓ [{u['uid']}] {tipo} → {u['email']}")
        else:
            stats["errores"] += 1

    print(
        f"\n[onboarding-notif] Fin:"
        f"\n  Sin perfil       → {stats['sin_perfil']}"
        f"\n  Perfil incompleto→ {stats['incompleto']}"
        f"\n  Sin autopilot    → {stats['sin_autopilot']}"
        f"\n  Omitidos (CD)    → {stats['omitidos']}"
        f"\n  Errores          → {stats['errores']}"
    )
    return stats


def send_test(to: str = "bastian.alfaro@gmail.com"):
    """Envía los 3 tipos de email a una dirección de prueba para revisar el diseño."""
    print(f"[test] Enviando 3 emails de prueba a {to}...")

    casos = [
        ("sin_perfil",        "Tu perfil está incompleto — activa el autopilot en AplicAI",
         _html_sin_perfil("Juan")),
        ("perfil_incompleto", "Falta poco — completa tu perfil en AplicAI",
         _html_perfil_incompleto("María", ["Cargos que buscas", "CV subido"])),
        ("sin_autopilot",     "¡Tu perfil está listo — activa el autopilot!",
         _html_sin_autopilot("Carlos")),
    ]

    for tipo, subject, html in casos:
        ok = _send(to, f"[TEST {tipo}] {subject}", html)
        print(f"  {'✓' if ok else '✗'} {tipo}")

    print("[test] Listo — revisa tu bandeja de entrada.")


if __name__ == "__main__":
    import sys
    # --dry-run imprime a quien se enviaria, sin enviar ni escribir en BQ
    _dry = "--dry-run" in sys.argv
    if _dry:
        print("[dry-run] simulacion: no se envia ningun correo")
    run(dry_run=_dry)
