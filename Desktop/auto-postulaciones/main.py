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
from portal_accounts    import get_or_create_account, get_trabajando_session, close_trabajando_session
from matcher            import filter_jobs
from telegram_notify    import enviar as telegram

MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "3"))

# Límites por plan
PLAN_LIMITS: dict[str, dict] = {
    "FREE":    {"jobs_per_cargo": 15,  "max_ubicaciones": 1, "max_postulaciones_dia": 10, "max_email": 5},
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


def process_user(user: dict) -> dict:
    # ID_USUARIO puede venir en mayúsculas o minúsculas dependiendo del origen
    uid    = user.get("ID_USUARIO") or user.get("id_usuario") or ""
    nombre = user.get("NOMBRE") or user.get("nombre") or uid
    plan   = user.get("plan", "FREE")
    limits = _get_limits(plan)

    cargos      = _parse_json(user.get("cargos") or user.get("CARGOS"))
    ubicaciones = _parse_json(user.get("ubicaciones") or user.get("UBICACIONES"))

    if not cargos or not ubicaciones:
        print(f"[{nombre}] Sin cargos/ubicaciones — omitido")
        return {"user": nombre, "found": 0, "applied": 0}

    print(f"\n[{nombre}] Plan: {plan} | cargos: {cargos} / ubicaciones: {ubicaciones}")
    print(f"  Límites → jobs/cargo: {limits['jobs_per_cargo']} | ubicaciones: {limits['max_ubicaciones']} | emails: {limits['max_email']}")

    # ── 0. Cuentas y sesión Selenium (antes de scraping — Trabajando.cl lo necesita) ──
    portal_creds: dict[str, dict | None] = {}
    for portal in ["trabajando"]:
        portal_creds[portal] = get_or_create_account(user, portal)

    tbj_driver = None
    tbj_creds = portal_creds.get("trabajando")
    if tbj_creds:
        tbj_driver = get_trabajando_session(uid, tbj_creds["email"], tbj_creds["password"])

    # ── 1. Descubrir empleos ────────────────────────────────────────────────
    all_jobs = []
    for cargo in cargos:
        for ubicacion in ubicaciones[:limits["max_ubicaciones"]]:
            jobs = find_jobs(cargo, ubicacion, n=limits["jobs_per_cargo"], trabajando_driver=tbj_driver)
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

    # ── 3. Postular ─────────────────────────────────────────────────────────
    applied_jobs: list[dict] = []
    rows_to_save: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    email_count = 0
    form_count  = 0
    max_dia     = limits["max_postulaciones_dia"]
    try:
        for job in new_jobs:
            total_aplicado = email_count + form_count
            if total_aplicado >= max_dia:
                print(f"[{nombre}] Límite diario alcanzado ({max_dia} postulaciones) — plan {plan}")
                break

            postulado = False

            # 1. Intentar postulación por email (rápido, sin browser)
            if email_count < limits["max_email"]:
                contact_email = extract_email(job.get("descripcion", ""))
                if contact_email:
                    ok = send_application(user, job, contact_email)
                    if ok:
                        postulado = True
                        applied_jobs.append(job)
                        email_count += 1
                        print(f"  ✓ Email → {contact_email} ({job['titulo'][:50]})")
                        telegram(
                            f"[PostulAI] Postulacion enviada\n"
                            f"Usuario: {nombre}\n"
                            f"Cargo: {job.get('titulo','')[:60]}\n"
                            f"Empresa: {job.get('empresa','') or 'N/A'}\n"
                            f"Via: Email ({contact_email})"
                        )

            # 2. Si no se pudo por email → formulario del portal
            if not postulado:
                fuente = job.get("fuente", "").lower()
                ok = apply_via_form(
                    user, job,
                    credentials=portal_creds.get(fuente),
                    trabajando_driver=tbj_driver if fuente == "trabajando" else None,
                )
                if ok:
                    postulado = True
                    applied_jobs.append(job)
                    form_count += 1
                    telegram(
                        f"[PostulAI] Postulacion enviada\n"
                        f"Usuario: {nombre}\n"
                        f"Cargo: {job.get('titulo','')[:60]}\n"
                        f"Empresa: {job.get('empresa','') or 'N/A'}\n"
                        f"Via: {fuente.capitalize()}"
                    )

            # Guardar en BigQuery sin importar si se postuló o no
            job_id = job.get("id") or job.get("link") or str(uuid.uuid4())
            rows_to_save.append({
                "id_empleo":         job_id[:1024],
                "id_usuario":        uid,
                "titulo_empleo":     job.get("titulo", "")[:500],
                "cargo":             job.get("_cargo", "")[:200],
                "Fecha_Postulacion": now_iso,
                "empresa":           job.get("empresa", "")[:500],
                "descripcion":       (job.get("descripcion") or "")[:5000],
                "link":              (job.get("link") or "")[:1024],
            })
    finally:
        close_trabajando_session(uid)

    # ── 4. Guardar en BigQuery ───────────────────────────────────────────────
    bq.save_jobs(rows_to_save)
    print(f"[{nombre}] Guardados {len(rows_to_save)} en BigQuery | Email: {email_count} | Formulario: {form_count}")

    # ── 5. Notificar al usuario ─────────────────────────────────────────────
    send_summary(user, new_jobs, applied_jobs)

    # ── 6. Resumen Telegram interno ─────────────────────────────────────────
    total = email_count + form_count
    if total > 0:
        telegram(
            f"[PostulAI] Resumen diario\n"
            f"{'─' * 22}\n"
            f"Usuario: {nombre}\n"
            f"Empleos encontrados: {len(new_jobs)}\n"
            f"Postulaciones: {total}\n"
            f"  Via email: {email_count}\n"
            f"  Via formulario: {form_count}"
        )
    else:
        telegram(
            f"[PostulAI] Sin postulaciones hoy\n"
            f"Usuario: {nombre}\n"
            f"Empleos revisados: {len(new_jobs)}"
        )

    return {"user": nombre, "found": len(new_jobs), "applied": email_count + form_count,
            "email": email_count, "form": form_count}


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

    print(f"\n{'─'*50}")
    print(f"✅ Proceso terminado en {elapsed}s")
    print(f"   Usuarios procesados  : {len(results)}")
    print(f"   Empleos descubiertos : {total_found}")
    print(f"   Postulaciones email  : {total_email}")
    print(f"   Postulaciones form   : {total_form}")
    print(f"   Total postulaciones  : {total_email + total_form}")
    print(f"{'─'*50}")


if __name__ == "__main__":
    main()
