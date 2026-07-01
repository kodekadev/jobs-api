"""
Auto-postulaciones Jobs
─────────────────────────────────────────────
Corre como Cloud Run Job, disparado por Cloud Scheduler.
Flujo:
  1. Lee usuarios activos desde BigQuery
  2. Por cada usuario: busca empleos con jobspy (LinkedIn + Indeed)
  3. Filtra empleos nuevos (no aplicados antes)
  4. Si el empleo tiene email → postula por email con CV adjunto
  5. Guarda todos los empleos encontrados en BigQuery EMPLEOS
  6. Envía resumen al usuario por email
"""

import os
import json
import time
import random
import uuid
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import bq
from scraper            import find_jobs
from emailer            import extract_email, send_application
from applier            import apply_via_form
from notifier           import send_summary, send_trial_warning
from portal_accounts    import get_or_create_account, get_trabajando_session, close_trabajando_session, get_trabajando_pw_session
from matcher            import filter_jobs
from telegram_notify    import enviar as telegram

MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "3"))

# Límites por plan
PLAN_LIMITS: dict[str, dict] = {
    "FREE":    {"jobs_per_cargo": 25,  "max_ubicaciones": 1, "max_postulaciones_dia": 10, "max_email": 5},
    "PRO":     {"jobs_per_cargo": 50,  "max_ubicaciones": 3, "max_postulaciones_dia": 25, "max_email": 15},
    "PREMIUM": {"jobs_per_cargo": 100, "max_ubicaciones": 5, "max_postulaciones_dia": 50, "max_email": 25},
    "TRIAL":   {"jobs_per_cargo": 50,  "max_ubicaciones": 3, "max_postulaciones_dia": 25, "max_email": 15},
}
DEFAULT_LIMITS = PLAN_LIMITS["FREE"]


def _get_limits(plan: str) -> dict:
    return PLAN_LIMITS.get(plan.upper(), DEFAULT_LIMITS)


def _parse_json(val) -> list:
    if not val:
        return []
    if isinstance(val, list):
        return val
    try:
        return json.loads(val)
    except Exception:
        return []


def _dedupe(jobs: list[dict]) -> list[dict]:
    seen, out = set(), []
    for j in jobs:
        key = j.get("id") or j.get("link")
        if key and key not in seen:
            seen.add(key)
            out.append(j)
    return out


CAMPOS_REQUERIDOS = {
    "cargos":             "Cargos de búsqueda",
    "ubicaciones":        "Ubicaciones",
    "profesion":          "Profesión",
    "pretension_general": "Pretensión de renta",
    "cv_url":             "CV (archivo PDF)",
    "rut":                "RUT",
    "fecha_nacimiento":   "Fecha de nacimiento",
    "experiencia":        "Años de experiencia",
    "empresa":            "Empresa actual o anterior",
    "nivel_educativo":    "Nivel educativo",
    "institucion":        "Institución educativa",
    "carrera":            "Carrera / Título",
    "situacion_estudios": "Situación de estudios",
}


def _validar_perfil(user: dict) -> list[str]:
    """Retorna lista de nombres de campos faltantes. Vacía = perfil completo."""
    faltantes = []
    for campo, label in CAMPOS_REQUERIDOS.items():
        # BigQuery devuelve claves en mayúsculas; soportar ambos casos
        val = user.get(campo) or user.get(campo.upper())
        if not val:
            faltantes.append(label)
        elif campo in ("cargos", "ubicaciones"):
            if not _parse_json(val):
                faltantes.append(label)
    return faltantes


def process_user(user: dict) -> dict:
    uid    = user.get("ID_USUARIO") or user.get("id_usuario") or ""
    nombre = user.get("NOMBRE") or user.get("nombre") or uid
    plan   = user.get("plan", "FREE")
    limits = _get_limits(plan)

    faltantes = _validar_perfil(user)
    if faltantes:
        print(f"[{nombre}] Perfil incompleto — omitido. Faltan: {', '.join(faltantes)}")
        return {"user": nombre, "found": 0, "applied": 0}

    cargos      = _parse_json(user.get("cargos") or user.get("CARGOS"))
    ubicaciones = _parse_json(user.get("ubicaciones") or user.get("UBICACIONES"))

    print(f"\n[{nombre}] Plan: {plan} | cargos: {cargos} / ubicaciones: {ubicaciones}")
    print(f"  Límites → jobs/cargo: {limits['jobs_per_cargo']} | ubicaciones: {limits['max_ubicaciones']} | emails: {limits['max_email']}")

    # ── 0. Cuentas y sesión (Playwright principal, Selenium fallback) ──────────
    portal_creds: dict[str, dict | None] = {}
    for portal in ["chiletrabajos", "trabajando"]:
        portal_creds[portal] = get_or_create_account(user, portal)

    tbj_creds = portal_creds.get("trabajando")

    # Intentar Playwright primero (más confiable, sin Xvfb)
    tbj_page = None
    tbj_driver = None
    if tbj_creds:
        tbj_page = get_trabajando_pw_session(uid, tbj_creds["email"], tbj_creds["password"])
        if not tbj_page:
            # Fallback a Selenium si no hay cookies
            tbj_driver = get_trabajando_session(uid, tbj_creds["email"], tbj_creds["password"])

    # Sesión ChileTrabajos (Selenium) — postular logueado en vez de como invitado
    cht_creds  = portal_creds.get("chiletrabajos")
    cht_driver = None
    if cht_creds:
        from chiletrabajos.login import get_session as get_cht_session
        cht_driver = get_cht_session(uid, cht_creds["email"], cht_creds["password"])

    # Cookies LinkedIn (guardadas por sesión manual previa)
    linkedin_cookies = bq.get_portal_cookies(uid, "linkedin") or []
    if linkedin_cookies:
        print(f"  -> LinkedIn: {len(linkedin_cookies)} cookies de sesión disponibles")

    # ── 1. Descubrir empleos ────────────────────────────────────────────────
    all_jobs = []
    for cargo in cargos:
        for ubicacion in ubicaciones[:limits["max_ubicaciones"]]:
            jobs = find_jobs(cargo, ubicacion, n=limits["jobs_per_cargo"], trabajando_driver=tbj_driver, chiletrabajos_driver=cht_driver)
            for j in jobs:
                j["_cargo"] = cargo
            all_jobs.extend(jobs)
            time.sleep(random.uniform(1, 3))

    unique_jobs = _dedupe(all_jobs)
    print(f"[{nombre}] {len(unique_jobs)} empleos únicos encontrados")

    if not unique_jobs:
        return {"user": nombre, "found": 0, "applied": 0}

    # ── 2. Filtrar ya aplicados ─────────────────────────────────────────────
    applied_before = bq.get_applied_job_ids(uid)
    new_jobs = [
        j for j in unique_jobs
        if (j.get("id") or j.get("link")) not in applied_before
    ]
    print(f"[{nombre}] {len(new_jobs)} empleos nuevos (no aplicados antes)")

    # ── 2b. Filtro de relevancia (keyword + LLM) ────────────────────────────
    new_jobs = filter_jobs(user, new_jobs)
    print(f"[{nombre}] {len(new_jobs)} empleos tras filtro de relevancia")

    # Mezclar para distribuir equitativamente entre portales (no dejar ChileTrabajos siempre al final)
    random.shuffle(new_jobs)

    # ── 3. Postular ─────────────────────────────────────────────────────────
    applied_jobs: list[dict] = []
    rows_to_save: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    max_dia = limits["max_postulaciones_dia"]

    # Cuota por canal: dividir el límite diario equitativamente entre los 3 canales
    # Canal email  → empleos de LinkedIn/Indeed (descripciones completas, a veces traen email de contacto)
    # Canal form   → trabajando.cl y chiletrabajos.cl (formulario web)
    _num_canales = 3
    _cuota_base  = max_dia // _num_canales
    _extra       = max_dia  % _num_canales
    channel_limits = {
        "email":         _cuota_base + (1 if _extra > 0 else 0),
        "trabajando":    _cuota_base + (1 if _extra > 1 else 0),
        "chiletrabajos": _cuota_base,
    }
    channel_counts: dict[str, int] = {k: 0 for k in channel_limits}
    print(f"[{nombre}] Cuotas por canal: " + " | ".join(f"{k.capitalize()}: {v}" for k, v in channel_limits.items()))

    email_count   = 0
    form_count    = 0
    portal_counts: dict[str, int] = {}

    def _save_aplicacion(job: dict, fuente: str, empresa: str = "", descripcion: str = "") -> None:
        """Guarda postulación en BigQuery y envía notificación."""
        bq.save_notificacion(
            user_id=uid,
            titulo=job.get("titulo", ""),
            empresa=empresa,
            link=job.get("link", ""),
            portal=fuente,
        )
        job_id = job.get("id") or job.get("link") or str(uuid.uuid4())
        rows_to_save.append({
            "id_empleo":         job_id[:1024],
            "id_usuario":        uid,
            "titulo_empleo":     job.get("titulo", "")[:500],
            "cargo":             job.get("_cargo", "")[:200],
            "Fecha_Postulacion": now_iso,
            "empresa":           empresa[:500],
            "descripcion":       descripcion[:5000],
            "link":              (job.get("link") or "")[:1024],
            "portal":            fuente[:100],
        })

    def _check_selenium_driver(driver, label: str):
        """Devuelve el driver si está vivo, None si la sesión murió.
        Intenta recuperar 'no such window' cambiando de handle antes de declararlo muerto."""
        if driver is None:
            return None
        try:
            _ = driver.current_url
            return driver
        except Exception as e:
            err = str(e).lower()
            if "no such window" in err:
                # Chrome cerró la pestaña activa pero la sesión puede seguir viva
                try:
                    handles = driver.window_handles
                    if handles:
                        driver.switch_to.window(handles[-1])
                        print(f"  ! [{label}] Ventana cerrada — cambiado a handle {handles[-1][:8]}")
                        return driver
                except Exception:
                    pass
            print(f"  ! [{label}] Sesión Selenium muerta ({err[:60]}) — canal deshabilitado")
            return None

    def _check_playwright_page(page, label: str):
        """Devuelve la page si está viva, None si crasheó."""
        if page is None:
            return None
        try:
            _ = page.url
            return page
        except Exception as e:
            print(f"  ! [{label}] Página Playwright muerta ({str(e)[:60]}) — canal deshabilitado")
            return None

    try:
        for job in new_jobs:
            total_aplicado = sum(channel_counts.values())
            if total_aplicado >= max_dia:
                print(f"[{nombre}] Límite diario alcanzado ({max_dia}) — plan {plan}")
                break

            fuente = job.get("fuente", "").lower()

            # ── Canal Email: empleos LinkedIn/Indeed con email de contacto ──────
            # Hard cap: no saturar de emails. Si este canal no rinde (pocos emails),
            # los slots libres los absorben automáticamente los canales form.
            if fuente in ("linkedin", "indeed"):
                if channel_counts["email"] >= channel_limits["email"]:
                    continue
                contact_email = extract_email(job.get("descripcion", ""))
                if not contact_email:
                    continue
                ok = send_application(user, job, contact_email)
                if ok:
                    applied_jobs.append(job)
                    email_count += 1
                    channel_counts["email"] += 1
                    portal_counts["email"] = portal_counts.get("email", 0) + 1
                    _save_aplicacion(job, "email", job.get("empresa", ""), job.get("descripcion", ""))
                    print(f"  OK Email -> {contact_email} ({job['titulo'][:50]})")

            # ── Canal Form: trabajando.cl y chiletrabajos.cl ─────────────────
            # Cuota suave por canal: si uno cae (browser muerto, sin empleos),
            # el otro absorbe los slots restantes hasta completar max_dia.
            elif fuente in ("trabajando", "chiletrabajos"):
                canal = fuente

                # ── Health check: detectar sesión Selenium/Playwright muerta ──
                if fuente == "chiletrabajos":
                    cht_driver = _check_selenium_driver(cht_driver, "cht")
                    if cht_driver is None:
                        continue  # canal cht muerto, próxima iteración puede ser tbj
                elif fuente == "trabajando":
                    tbj_page   = _check_playwright_page(tbj_page, "tbj-pw")
                    if tbj_page is None and tbj_driver is not None:
                        tbj_driver = _check_selenium_driver(tbj_driver, "tbj-sel")
                    if tbj_page is None and tbj_driver is None:
                        continue  # canal tbj completamente muerto

                # Cuota suave: respetar límite propio, pero permitir overflow si
                # otros canales dejaron slots libres sin usar
                cuota_propia = channel_limits.get(canal, _cuota_base)
                if channel_counts[canal] >= cuota_propia:
                    slots_email_libres = max(0, channel_limits["email"] - channel_counts["email"])
                    if slots_email_libres <= 0:
                        continue  # email también cubierto, no hay slots que absorber

                result = apply_via_form(
                    user, job,
                    credentials=portal_creds.get(fuente),
                    trabajando_driver=tbj_driver if fuente == "trabajando" else None,
                    trabajando_page=tbj_page   if fuente == "trabajando" else None,
                    chiletrabajos_driver=cht_driver if fuente == "chiletrabajos" else None,
                    linkedin_cookies=None,
                )
                if result:
                    applied_jobs.append(job)
                    form_count += 1
                    channel_counts[canal] += 1
                    portal_counts[fuente] = portal_counts.get(fuente, 0) + 1

                    job_empresa     = (result.get("empresa")     if isinstance(result, dict) else None) or job.get("empresa", "")
                    job_descripcion = (result.get("descripcion") if isinstance(result, dict) else None) or job.get("descripcion", "")
                    _save_aplicacion(job, fuente, job_empresa, job_descripcion)

    finally:
        close_trabajando_session(uid)
        if cht_driver:
            try:
                cht_driver.quit()
            except Exception:
                pass

    # ── 4. Guardar en BigQuery ───────────────────────────────────────────────
    bq.save_jobs(rows_to_save)
    canal_str = " | ".join(f"{k.capitalize()} {channel_counts[k]}/{channel_limits[k]}" for k in ("email", "trabajando", "chiletrabajos"))
    print(f"[{nombre}] Guardados {len(rows_to_save)} en BigQuery | {canal_str}")

    # ── 5. Notificar al usuario ─────────────────────────────────────────────
    send_summary(user, new_jobs, applied_jobs)

    # ── 6. Resumen Telegram (un solo mensaje al final) ──────────────────────
    total = email_count + form_count
    max_dia = limits["max_postulaciones_dia"]
    if total > 0:
        lineas = [
            f"[PostulAI] {total} postulaciones enviadas",
            f"Usuario: {nombre} | Plan: {plan}",
            f"{'─' * 30}",
        ]
        for i, j in enumerate(applied_jobs[:30], 1):
            empresa = j.get("empresa") or ""
            titulo  = j.get("titulo", "")[:55]
            lineas.append(f"{i}. {titulo}" + (f" — {empresa[:30]}" if empresa else ""))
        if len(applied_jobs) > 30:
            lineas.append(f"... y {len(applied_jobs) - 30} más")
        lineas.append(f"{'─' * 30}")
        portal_detail = " | ".join(f"{p.capitalize()}: {c}" for p, c in sorted(portal_counts.items()))
        cuotas_str = " | ".join(f"{k.capitalize()} {channel_counts[k]}/{channel_limits[k]}" for k in ("email", "trabajando", "chiletrabajos"))
        lineas.append(f"Canales: {cuotas_str} | Límite: {max_dia}/día")
        telegram("\n".join(lineas))
    else:
        telegram(
            f"[PostulAI] Sin postulaciones hoy\n"
            f"Usuario: {nombre} | Plan: {plan}\n"
            f"Empleos revisados: {len(new_jobs)}"
        )

    return {"user": nombre, "found": len(new_jobs), "applied": email_count + form_count,
            "email": email_count, "form": form_count, "portales": portal_counts}


def main():
    start = datetime.now()
    print(f"🚀 Auto-postulaciones iniciadas: {start.strftime('%Y-%m-%d %H:%M')} UTC\n")

    single_user_id = os.environ.get("SINGLE_USER_ID", "").strip()

    users = bq.get_active_users()

    # Si se especificó un usuario concreto (trigger inmediato desde el backend),
    # filtramos solo ese usuario aunque no tenga activo=1 todavía
    if single_user_id:
        users = [u for u in users if u["ID_USUARIO"].lower() == single_user_id.lower()]
        if not users:
            # Puede que aún no esté en BigQuery como activo, intentar obtenerlo igual
            users = bq.get_user_by_id(single_user_id)
        print(f"Modo usuario único: {single_user_id} ({len(users)} encontrado)\n")
    else:
        print(f"Usuarios activos: {len(users)}\n")

    if not users:
        print("No hay usuarios para procesar. Fin.")
        return

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_user, u): u for u in users}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                u = futures[future]
                print(f"ERROR procesando {u.get('NOMBRE', u.get('ID_USUARIO'))}: {e}")

    # Avisos de vencimiento de trial (4 días antes)
    if not single_user_id:
        try:
            expiring = bq.get_expiring_trials(days=4)
            for u in expiring:
                send_trial_warning(u, days_left=4)
            if expiring:
                print(f"  Avisos de trial enviados: {len(expiring)}")
        except Exception as e:
            print(f"  ⚠ Error en avisos trial: {e}")

    elapsed = (datetime.now() - start).seconds
    total_found   = sum(r["found"]      for r in results)
    total_email   = sum(r.get("email",0) for r in results)
    total_form    = sum(r.get("form", 0)  for r in results)

    # Aggregate per-portal counts
    all_portals: dict[str, int] = {}
    for r in results:
        for p, c in r.get("portales", {}).items():
            all_portals[p] = all_portals.get(p, 0) + c

    print(f"\n{'─'*50}")
    print(f"✅ Proceso terminado en {elapsed}s")
    print(f"   Usuarios procesados  : {len(results)}")
    print(f"   Empleos descubiertos : {total_found}")
    print(f"   Postulaciones email  : {total_email}")
    print(f"   Postulaciones form   : {total_form}")
    for portal, cnt in sorted(all_portals.items()):
        print(f"     └─ {portal.capitalize():<20}: {cnt}")
    print(f"   Total postulaciones  : {total_email + total_form}")
    print(f"{'─'*50}")


if __name__ == "__main__":
    main()
