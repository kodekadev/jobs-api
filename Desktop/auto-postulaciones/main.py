"""
Auto-postulaciones Jobs
─────────────────────────────────────────────
Corre como Cloud Run Job, disparado por Cloud Scheduler.
Flujo:
  1. Lee usuarios activos desde BigQuery
  2. Por cada usuario: busca empleos en Trabajando.cl y ChileTrabajos
  3. Filtra empleos nuevos (no aplicados antes)
  4. Postula via formulario web en cada portal
  5. Guarda postulaciones en BigQuery
  6. Envía resumen al usuario por email
"""

import os
import json
import time
import random
import uuid
import math
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, wait

# Timeout máximo por usuario (segundos). Si un usuario cuelga más que esto,
# se cancela el future y se reporta timeout. Default 20 minutos.
USER_TIMEOUT_S = int(os.environ.get("USER_TIMEOUT_SECONDS", "1200"))

import bq
from scraper            import find_jobs
from applier            import apply_via_form
from notifier           import send_summary, send_trial_warning
from portal_accounts    import (
    get_or_create_account,
    get_trabajando_session, close_trabajando_session, get_trabajando_pw_session,
    get_chiletrabajos_pw_session, close_chiletrabajos_pw_session,
)
from matcher            import filter_jobs
from telegram_notify    import enviar as telegram

MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "3"))

# Límites por plan
PLAN_LIMITS: dict[str, dict] = {
    "FREE":    {"jobs_per_cargo": 25,  "max_ubicaciones": 1,  "max_postulaciones_dia": 10, "max_email": 5},
    "PRO":     {"jobs_per_cargo": 50,  "max_ubicaciones": 4,  "max_postulaciones_dia": 25, "max_email": 15},
    "PREMIUM": {"jobs_per_cargo": 100, "max_ubicaciones": 10, "max_postulaciones_dia": 50, "max_email": 25},
    "TRIAL":   {"jobs_per_cargo": 50,  "max_ubicaciones": 4,  "max_postulaciones_dia": 25, "max_email": 15},
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

# user=u
def process_user(user: dict) -> dict:
    uid    = user.get("ID_USUARIO") or user.get("id_usuario") or ""
    nombre = user.get("NOMBRE") or user.get("nombre") or uid
    plan   = user.get("plan", "FREE")
    limits = _get_limits(plan)

    print(f"\n[{nombre}] Iniciando proceso — {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC", flush=True)

    faltantes = _validar_perfil(user)
    if faltantes:
        print(f"[{nombre}] Perfil incompleto — omitido. Faltan: {', '.join(faltantes)}", flush=True)
        return {"user": nombre, "found": 0, "applied": 0}

    cargos      = _parse_json(user.get("cargos") or user.get("CARGOS"))
    ubicaciones = _parse_json(user.get("ubicaciones") or user.get("UBICACIONES"))

    print(f"[{nombre}] Plan: {plan} | cargos: {cargos} / ubicaciones: {ubicaciones}", flush=True)
    print(f"  Límites → jobs/cargo: {limits['jobs_per_cargo']} | ubicaciones: {limits['max_ubicaciones']} | emails: {limits['max_email']}", flush=True)

    # ── 0. Cuentas y sesión (Playwright principal, Selenium fallback) ──────────
    print(f"[{nombre}] Obteniendo cuentas de portales...", flush=True)
    _env_portales = os.environ.get("PORTALES_ACTIVOS", "").strip()
    _portales_lista = ["chiletrabajos", "trabajando"]
    portales_activos = [p for p in _portales_lista if not _env_portales or p in _env_portales.lower().split(",")]

    portal_creds: dict[str, dict | None] = {}
    for portal in portales_activos:
        portal_creds[portal] = get_or_create_account(user, portal)

    tbj_creds = portal_creds.get("trabajando")

    # Alerta si las cookies de Trabajando.cl están por vencer (< 24h restantes de las 120h)
    if tbj_creds:
        age = bq.get_cookie_age_hours(uid, "trabajando")
        if age is not None and age >= 96:
            horas_restantes = max(0, 120 - age)
            telegram(
                f"⚠️ [AplicAI] Cookies Trabajando.cl vencen pronto\n"
                f"Usuario: {nombre} ({uid})\n"
                f"Quedan ~{horas_restantes:.0f}h (TTL: 120h).\n"
                f"Ejecutar: python save_trabajando_session.py {uid}"
            )

    # Intentar Playwright primero (más confiable, sin Xvfb)
    tbj_page = None
    tbj_driver = None
    if tbj_creds:
        print(f"[{nombre}] Iniciando sesión Trabajando.cl (Playwright)...", flush=True)
        tbj_page = get_trabajando_pw_session(uid, tbj_creds["email"], tbj_creds["password"])
        if not tbj_page:
            print(f"[{nombre}] Sin cookies PW — intentando Selenium...", flush=True)
            tbj_driver = get_trabajando_session(uid, tbj_creds["email"], tbj_creds["password"])

    # Sesión ChileTrabajos (Playwright via cookies BQ)
    cht_creds = portal_creds.get("chiletrabajos")
    cht_page  = None
    if cht_creds:
        print(f"[{nombre}] Iniciando sesión ChileTrabajos (Playwright)...", flush=True)
        cht_page = get_chiletrabajos_pw_session(uid, cht_creds["email"], cht_creds["password"])

    # Cookies LinkedIn (guardadas por sesión manual previa)
    linkedin_cookies = bq.get_portal_cookies(uid, "linkedin") or []
    if linkedin_cookies:
        print(f"  -> LinkedIn: {len(linkedin_cookies)} cookies de sesión disponibles")

    # ── 1. Setup de tracking ────────────────────────────────────────────────
    applied_before = bq.get_applied_job_ids(uid)
    applied_ids    = set(applied_before)  # crece a medida que postulamos

    excluidas_raw = user.get("EMPRESAS_EXCLUIDAS") or user.get("empresas_excluidas") or "[]"
    try:
        import json as _json
        excluidas = [e.strip().lower() for e in (_json.loads(excluidas_raw) if isinstance(excluidas_raw, str) else excluidas_raw) if e]
    except Exception:
        excluidas = []

    applied_jobs:  list[dict] = []
    rows_to_save:  list[dict] = []
    now_iso        = datetime.now(timezone.utc).isoformat()
    max_dia        = limits["max_postulaciones_dia"]
    _cuota_base    = max_dia // 2
    _extra         = max_dia  % 2
    channel_limits = {"trabajando": _cuota_base + _extra, "chiletrabajos": _cuota_base}
    channel_counts: dict[str, int] = {k: 0 for k in channel_limits}
    form_count     = 0
    portal_counts: dict[str, int] = {}

    print(f"[{nombre}] Cuotas por canal: " + " | ".join(f"{k.capitalize()}: {v}" for k, v in channel_limits.items()), flush=True)

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
        if driver is None:
            return None
        try:
            _ = driver.current_url
            return driver
        except Exception as e:
            err = str(e).lower()
            if "no such window" in err:
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
        if page is None:
            return None
        try:
            _ = page.url
            return page
        except Exception as e:
            print(f"  ! [{label}] Página Playwright muerta ({str(e)[:60]}) — canal deshabilitado")
            return None

    # ── 2. Por cargo: buscar → filtrar → postular ────────────────────────────
    print(f"[{nombre}] Buscando y postulando por cargo...", flush=True)
    try:
        for cargo in cargos:
            if sum(channel_counts.values()) >= max_dia:
                print(f"[{nombre}] Límite diario alcanzado ({max_dia}) — deteniendo", flush=True)
                break

            # Buscar empleos para este cargo
            cargo_jobs: list[dict] = []
            for ubicacion in ubicaciones[:limits["max_ubicaciones"]]:
                print(f"  [{nombre}] Buscando '{cargo}' en '{ubicacion}'...", flush=True)
                try:
                    jobs = find_jobs(cargo, ubicacion, n=limits["jobs_per_cargo"],
                                     trabajando_page=tbj_page, trabajando_driver=tbj_driver,
                                     chiletrabajos_page=cht_page)
                except Exception as e:
                    print(f"  [{nombre}] find_jobs error '{cargo}': {e}", flush=True)
                    jobs = []
                for j in jobs:
                    j["_cargo"] = cargo
                cargo_jobs.extend(jobs)
                time.sleep(random.uniform(1, 3))

            cargo_jobs = _dedupe(cargo_jobs)
            print(f"  [{nombre}] '{cargo}': {len(cargo_jobs)} empleos encontrados", flush=True)

            # Filtrar ya aplicados (incluyendo los de esta misma ejecución)
            cargo_new = [j for j in cargo_jobs if (j.get("id") or j.get("link")) not in applied_ids]

            # Filtro de relevancia (keyword + LLM)
            cargo_new = filter_jobs(user, cargo_new)

            # Filtro lista negra
            if excluidas:
                antes = len(cargo_new)
                cargo_new = [j for j in cargo_new if j.get("empresa", "").strip().lower() not in excluidas]
                if antes != len(cargo_new):
                    print(f"  [{nombre}] Lista negra: {antes - len(cargo_new)} excluidos", flush=True)

            random.shuffle(cargo_new)
            print(f"  [{nombre}] '{cargo}': {len(cargo_new)} empleos para postular", flush=True)

            # Postular a los empleos de este cargo
            for job in cargo_new:
                if sum(channel_counts.values()) >= max_dia:
                    print(f"[{nombre}] Límite diario alcanzado ({max_dia}) — plan {plan}", flush=True)
                    break

                fuente = job.get("fuente", "").lower()

                if fuente in ("trabajando", "chiletrabajos"):
                    canal = fuente

                    if fuente == "chiletrabajos":
                        cht_page = _check_playwright_page(cht_page, "cht-pw")
                        if cht_page is None:
                            continue
                    elif fuente == "trabajando":
                        tbj_page   = _check_playwright_page(tbj_page, "tbj-pw")
                        if tbj_page is None and tbj_driver is not None:
                            tbj_driver = _check_selenium_driver(tbj_driver, "tbj-sel")
                        if tbj_page is None and tbj_driver is None:
                            continue

                    cuota_propia = channel_limits.get(canal, _cuota_base)
                    if channel_counts[canal] >= cuota_propia:
                        otro = "chiletrabajos" if canal == "trabajando" else "trabajando"
                        slots_libres = max(0, channel_limits[otro] - channel_counts[otro])
                        if slots_libres <= 0:
                            continue

                    result = apply_via_form(
                        user, job,
                        credentials=portal_creds.get(fuente),
                        trabajando_driver=tbj_driver if fuente == "trabajando" else None,
                        trabajando_page=tbj_page    if fuente == "trabajando" else None,
                        chiletrabajos_page=cht_page if fuente == "chiletrabajos" else None,
                        linkedin_cookies=None,
                    )
                    success = result.get("ok", True) if isinstance(result, dict) else bool(result)
                    if success:
                        applied_jobs.append(job)
                        form_count += 1
                        channel_counts[canal] += 1
                        portal_counts[fuente] = portal_counts.get(fuente, 0) + 1

                        job_id = job.get("id") or job.get("link")
                        if job_id:
                            applied_ids.add(job_id)

                        job_empresa     = (result.get("empresa")     if isinstance(result, dict) else None) or job.get("empresa", "")
                        job_descripcion = (result.get("descripcion") if isinstance(result, dict) else None) or job.get("descripcion", "")
                        _save_aplicacion(job, fuente, job_empresa, job_descripcion)
                        print(f"  [{nombre}] ✓ Postulado: {job.get('titulo','')[:50]} ({fuente})", flush=True)

    finally:
        close_trabajando_session(uid)
        close_chiletrabajos_pw_session(uid)

    # ── 4. Guardar en BigQuery ───────────────────────────────────────────────
    bq.save_jobs(rows_to_save)
    canal_str = " | ".join(f"{k.capitalize()} {channel_counts[k]}/{channel_limits[k]}" for k in ("trabajando", "chiletrabajos"))
    print(f"[{nombre}] Guardados {len(rows_to_save)} en BigQuery | {canal_str}", flush=True)

    # ── 5. Notificar al usuario ─────────────────────────────────────────────
    send_summary(user, applied_jobs, applied_jobs)

    # ── 6. Resumen Telegram (un solo mensaje al final) ──────────────────────
    total = form_count
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
        cuotas_str = " | ".join(f"{k.capitalize()} {channel_counts[k]}/{channel_limits[k]}" for k in ("trabajando", "chiletrabajos"))
        lineas.append(f"Canales: {cuotas_str} | Límite: {max_dia}/día")
        telegram("\n".join(lineas))
    else:
        telegram(
            f"[PostulAI] Sin postulaciones hoy\n"
            f"Usuario: {nombre} | Plan: {plan}\n"
            f"Empleos revisados: {len(new_jobs)}"
        )

    return {"user": nombre, "found": len(new_jobs), "applied": form_count,
            "form": form_count, "portales": portal_counts}


def main():
    update_cv_mode = "--update-cv" in __import__("sys").argv
    start = datetime.now()

    if update_cv_mode:
        print(f"Modo --update-cv: {start.strftime('%Y-%m-%d %H:%M')} UTC\n", flush=True)
        single_user_id = os.environ.get("SINGLE_USER_ID", "").strip()
        users = bq.get_user_by_id(single_user_id) if single_user_id else bq.get_active_users()
        if not users:
            print("Sin usuarios para actualizar CV.", flush=True)
            return
        for u in users:
            update_cv_user(u)
        print(f"\nCV actualizado para {len(users)} usuario(s).", flush=True)
        return

    print(f"Auto-postulaciones iniciadas: {start.strftime('%Y-%m-%d %H:%M')} UTC\n", flush=True)

    single_user_id = os.environ.get("SINGLE_USER_ID", "").strip()

    users = bq.get_active_users()

    if single_user_id:
        users = [u for u in users if u["ID_USUARIO"].lower() == single_user_id.lower()]
        if not users:
            users = bq.get_user_by_id(single_user_id)
        print(f"Modo usuario único: {single_user_id} ({len(users)} encontrado)\n", flush=True)
    else:
        print(f"Usuarios activos: {len(users)}\n", flush=True)

    if not users:
        print("No hay usuarios para procesar. Fin.", flush=True)
        return

    # Timeout total: (batches de usuarios) * USER_TIMEOUT_S + buffer de 60s
    batches = math.ceil(len(users) / max(1, MAX_WORKERS))
    total_timeout = batches * USER_TIMEOUT_S + 60
    print(f"Timeout por lote: {USER_TIMEOUT_S}s | Lotes estimados: {batches} | Timeout total: {total_timeout}s", flush=True)

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_user, u): u for u in users}
        done, timed_out = wait(futures, timeout=total_timeout)

        for future in done:
            u = futures[future]
            nombre = u.get("NOMBRE", u.get("ID_USUARIO", "?"))
            try:
                results.append(future.result(timeout=10))
            except Exception as e:
                print(f"ERROR [{nombre}]: {e}", flush=True)
                results.append({"user": nombre, "found": 0, "applied": 0})

        for future in timed_out:
            u = futures[future]
            nombre = u.get("NOMBRE", u.get("ID_USUARIO", "?"))
            future.cancel()
            print(f"TIMEOUT [{nombre}] — excedió {USER_TIMEOUT_S}s/lote, cancelado", flush=True)
            results.append({"user": nombre, "found": 0, "applied": 0, "_timeout": True})
            try:
                telegram(f"⚠️ [AplicAI] TIMEOUT: usuario {nombre} colgado tras {USER_TIMEOUT_S}s — revisar logs")
            except Exception:
                pass

    # Avisos de vencimiento de trial (4 días antes)
    if not single_user_id:
        try:
            expiring = bq.get_expiring_trials(days=4)
            for u in expiring:
                send_trial_warning(u, days_left=4)
            if expiring:
                print(f"  Avisos de trial enviados: {len(expiring)}", flush=True)
        except Exception as e:
            print(f"  Error en avisos trial: {e}", flush=True)

    elapsed = (datetime.now() - start).seconds
    total_found   = sum(r["found"]      for r in results)
    total_form    = sum(r.get("form", 0) for r in results)
    timeouts      = sum(1 for r in results if r.get("_timeout"))

    all_portals: dict[str, int] = {}
    for r in results:
        for p, c in r.get("portales", {}).items():
            all_portals[p] = all_portals.get(p, 0) + c

    print(f"\n{'─'*50}", flush=True)
    print(f"Proceso terminado en {elapsed}s", flush=True)
    print(f"   Usuarios procesados  : {len(results)}", flush=True)
    if timeouts:
        print(f"   Timeouts             : {timeouts}", flush=True)
    print(f"   Empleos descubiertos : {total_found}", flush=True)
    print(f"   Postulaciones form   : {total_form}", flush=True)
    for portal, cnt in sorted(all_portals.items()):
        print(f"     └─ {portal.capitalize():<20}: {cnt}", flush=True)
    print(f"   Total postulaciones  : {total_form}", flush=True)
    print(f"{'─'*50}", flush=True)


def update_cv_user(user: dict) -> dict:
    """Actualiza el CV en ChileTrabajos y Trabajando.cl para un usuario."""
    uid    = user.get("ID_USUARIO") or user.get("id_usuario", "")
    nombre = user.get("NOMBRE", uid)
    ok_cht = ok_tbj = False

    try:
        from chiletrabajos.completar_perfil import _pw_completar_perfil_chiletrabajos
        ok_cht = _pw_completar_perfil_chiletrabajos(uid, user)
        print(f"  [{nombre}] ChileTrabajos CV update: {'OK' if ok_cht else 'SKIP'}", flush=True)
    except Exception as e:
        print(f"  [{nombre}] ChileTrabajos CV error: {e}", flush=True)

    try:
        from trabajando.completar_perfil import completar_perfil_trabajando
        ok_tbj = completar_perfil_trabajando(uid, user)
        print(f"  [{nombre}] Trabajando.cl CV update: {'OK' if ok_tbj else 'SKIP'}", flush=True)
    except Exception as e:
        print(f"  [{nombre}] Trabajando.cl CV error: {e}", flush=True)

    return {"user": nombre, "cht": ok_cht, "tbj": ok_tbj}


if __name__ == "__main__":
    main()
