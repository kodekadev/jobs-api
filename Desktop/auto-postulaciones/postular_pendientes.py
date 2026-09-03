"""
Procesa empleos aprobados en Modo Revisión y los postula automáticamente.

Corre como Jenkins job independiente (ej: cada 2 horas).
Lee los empleos con estado='aprobado' en EMPLEOS_PENDIENTES y los postula
usando las sesiones existentes de cada portal.
"""
import os
import sys
from collections import defaultdict

_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else r"C:\Users\bastian\Desktop\auto-postulaciones"
if _dir not in sys.path:
    sys.path.insert(0, _dir)

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")

from dotenv import load_dotenv
load_dotenv(os.path.join(_dir, ".env"))

import bq


def _postular_aprobados_cpt(user_id: str, user: dict, jobs: list[dict]) -> int:
    """Postula empleos aprobados de Computrabajo para un usuario."""
    import datetime, time, random
    try:
        from computrabajo.postular import (
            _make_pw_context, _new_stealth_page, _esta_logueado,
            _login_pw, _postular_uno, PORTAL_ID, CANDIDATO_URL,
        )
        from portal_accounts import _extract_cv_text
    except ImportError as e:
        print(f"  [pendientes-cpt] Import error: {e}")
        return 0

    cuenta = bq.get_portal_account(user_id, "computrabajo")
    if not cuenta:
        return 0

    ok_count = 0
    try:
        _, browser, ctx, _ = _make_pw_context()
        page = _new_stealth_page(ctx)
        try:
            cookies = bq.get_portal_cookies(user_id, "computrabajo")
            if cookies:
                ctx.add_cookies([c for c in cookies if isinstance(c, dict) and "name" in c and "value" in c])

            page.goto(f"{CANDIDATO_URL}/candidate/home", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            if not _esta_logueado(page):
                if not _login_pw(page, cuenta["email"], cuenta["password"], user_id=user_id):
                    return 0

            cv_text = ""
            try:
                cv_text = _extract_cv_text(user.get("CV_URL") or "") or ""
            except Exception:
                pass

            for job in jobs:
                url = job.get("url", "")
                if not url:
                    continue
                emp = {"link": url, "titulo": job.get("titulo", ""), "empresa": job.get("empresa", "")}
                ok, motivo = _postular_uno(page, emp, user, cv_text=cv_text, skip_llm=True)

                nuevo_estado = "postulado" if ok else "fallido"
                bq.update_pending_job_estado(job["id"], nuevo_estado)

                if ok:
                    bq.save_jobs([{
                        "id_empleo":         url,
                        "id_usuario":        user_id,
                        "titulo_empleo":     job.get("titulo", ""),
                        "cargo":             "",
                        "Fecha_Postulacion": datetime.datetime.utcnow().isoformat(),
                        "empresa":           job.get("empresa", ""),
                        "descripcion":       "",
                        "link":              url,
                        "portal":            "computrabajo",
                    }])
                    ok_count += 1
                    print(f"    ✓ [pendientes-cpt] {job.get('titulo','')[:50]}")
                else:
                    print(f"    ✗ [pendientes-cpt] {motivo}: {job.get('titulo','')[:50]}")

                time.sleep(random.uniform(3, 5))

            bq.save_portal_cookies(user_id, "computrabajo", ctx.cookies())
        finally:
            try:
                browser.close()
            except Exception:
                pass
    except Exception as e:
        import traceback
        print(f"  [pendientes-cpt] Error {user_id}: {e}")
        traceback.print_exc()

    return ok_count


def _postular_aprobados_lab(user_id: str, user: dict, jobs: list[dict]) -> int:
    """Postula empleos aprobados de Laborum para un usuario."""
    import datetime, time, random
    try:
        from laborum.postular import (
            _make_pw_context, _new_stealth_page, _esta_logueado,
            _login, _postular_uno, BASE_URL, PORTAL_ID, _SUELDO_DEFAULT,
        )
        from portal_accounts import _extract_cv_text
    except ImportError as e:
        print(f"  [pendientes-lab] Import error: {e}")
        return 0

    cuenta = bq.get_portal_account(user_id, "laborum")
    if not cuenta:
        return 0

    ok_count = 0
    salario = int(user.get("PRETENSION_GENERAL") or _SUELDO_DEFAULT)
    try:
        _, browser, ctx, _ = _make_pw_context()
        page = _new_stealth_page(ctx)
        try:
            cookies = bq.get_portal_cookies(user_id, "laborum")
            if cookies:
                ctx.add_cookies([c for c in cookies if isinstance(c, dict) and "name" in c and "value" in c])

            page.goto(f"{BASE_URL}/mi-cuenta", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            if not _esta_logueado(page):
                if not _login(page, cuenta["email"], cuenta["password"]):
                    return 0

            cv_text = ""
            try:
                cv_text = _extract_cv_text(user.get("CV_URL") or "") or ""
            except Exception:
                pass

            for job in jobs:
                url = job.get("url", "")
                if not url:
                    continue
                emp = {"link": url, "titulo": job.get("titulo", ""), "empresa": job.get("empresa", "")}
                ok, motivo = _postular_uno(page, emp, salario, user=user, cv_text=cv_text,
                                           email=cuenta["email"])

                nuevo_estado = "postulado" if ok else "fallido"
                bq.update_pending_job_estado(job["id"], nuevo_estado)

                if ok:
                    bq.save_jobs([{
                        "id_empleo":         url,
                        "id_usuario":        user_id,
                        "titulo_empleo":     job.get("titulo", ""),
                        "cargo":             "",
                        "Fecha_Postulacion": datetime.datetime.utcnow().isoformat(),
                        "empresa":           job.get("empresa", ""),
                        "descripcion":       "",
                        "link":              url,
                        "portal":            "laborum",
                    }])
                    ok_count += 1
                    print(f"    ✓ [pendientes-lab] {job.get('titulo','')[:50]}")
                else:
                    print(f"    ✗ [pendientes-lab] {motivo}: {job.get('titulo','')[:50]}")

                time.sleep(random.uniform(3, 6))

            bq.save_portal_cookies(user_id, "laborum", ctx.cookies())
        finally:
            try:
                browser.close()
            except Exception:
                pass
    except Exception as e:
        import traceback
        print(f"  [pendientes-lab] Error {user_id}: {e}")
        traceback.print_exc()

    return ok_count


def _postular_aprobados_cht(user_id: str, user: dict, jobs: list[dict]) -> int:
    """Postula empleos aprobados de ChileTrabajos para un usuario."""
    import datetime, time, random
    try:
        from chiletrabajos.postular import _postular_empleo_pw, PORTAL_ID
        from portal_accounts import get_chiletrabajos_pw_session, close_chiletrabajos_pw_session
    except ImportError as e:
        print(f"  [pendientes-cht] Import error: {e}")
        return 0

    cuenta = bq.get_portal_account(user_id, "chiletrabajos")
    if not cuenta:
        return 0

    ok_count = 0
    page = get_chiletrabajos_pw_session(user_id, cuenta["email"], cuenta["password"])
    if not page:
        print(f"  [pendientes-cht] Sin sesión para {user_id}")
        return 0

    try:
        for job in jobs:
            url = job.get("url", "")
            if not url:
                continue
            resultado = _postular_empleo_pw(page, url, user, job.get("titulo", ""))

            if resultado and isinstance(resultado, dict) and resultado.get("ok"):
                bq.update_pending_job_estado(job["id"], "postulado")
                bq.save_jobs([{
                    "id_empleo":         url,
                    "id_usuario":        user_id,
                    "titulo_empleo":     job.get("titulo", ""),
                    "cargo":             "",
                    "Fecha_Postulacion": datetime.datetime.utcnow().isoformat(),
                    "empresa":           job.get("empresa", ""),
                    "descripcion":       "",
                    "link":              url,
                    "portal":            "chiletrabajos",
                }])
                ok_count += 1
                print(f"    ✓ [pendientes-cht] {job.get('titulo','')[:50]}")
            else:
                bq.update_pending_job_estado(job["id"], "fallido")
                print(f"    ✗ [pendientes-cht] {job.get('titulo','')[:50]}")

            time.sleep(random.uniform(3, 6))
    finally:
        try:
            close_chiletrabajos_pw_session(user_id)
        except Exception:
            pass

    return ok_count


def _postular_aprobados_exc(user_id: str, user: dict, jobs: list[dict]) -> int:
    """Postula empleos aprobados de EmpleaXChile para un usuario."""
    import datetime, time, random
    try:
        from playwright.sync_api import sync_playwright
        from empleaxchile.postular import _postular_empleo, PORTAL_ID, BASE_URL
        from portal_accounts import _make_pw_context, _new_stealth_page
    except ImportError as e:
        print(f"  [pendientes-exc] Import error: {e}")
        return 0

    cuenta = bq.get_portal_account(user_id, "empleaxchile")
    if not cuenta:
        return 0

    email    = cuenta["email"]
    password = cuenta["password"]

    ok_count = 0
    try:
        pw_obj = sync_playwright().start()
        _, browser, ctx, _ = _make_pw_context(pw_obj)

        cookies = bq.get_portal_cookies(user_id, "empleaxchile")
        if cookies:
            try:
                ctx.add_cookies([c for c in cookies if isinstance(c, dict) and "name" in c and "value" in c])
            except Exception:
                pass

        page = _new_stealth_page(ctx)

        def _ensure_logged_in() -> bool:
            try:
                page.goto(f"{BASE_URL}/account/profile", wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(1500)
                if "login" not in page.url and "external" not in page.url:
                    return True
            except Exception:
                pass
            try:
                page.goto(f"{BASE_URL}/external/login", wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(1500)
                for sel in ["input[type='email']:visible", "input[name='Email']:visible"]:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() > 0:
                            loc.fill(email)
                            break
                    except Exception:
                        pass
                for sel in ["button:has-text('Continuar'):visible", "button[type='submit']:visible"]:
                    try:
                        btn = page.locator(sel).first
                        if btn.count() > 0:
                            btn.click()
                            page.wait_for_timeout(2000)
                            break
                    except Exception:
                        pass
                for sel in ["input[type='password']:visible"]:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() > 0:
                            loc.fill(password)
                            break
                    except Exception:
                        pass
                for sel in ["button[type='submit']:visible", "button:has-text('Iniciar'):visible"]:
                    try:
                        btn = page.locator(sel).first
                        if btn.count() > 0:
                            btn.click()
                            page.wait_for_timeout(5000)
                            break
                    except Exception:
                        pass
                return "login" not in page.url and "external" not in page.url
            except Exception as e:
                print(f"  [pendientes-exc] Error login: {e}")
                return False

        if not _ensure_logged_in():
            print(f"  [pendientes-exc] Login fallido para {user_id}")
            browser.close()
            pw_obj.stop()
            return 0

        try:
            for job in jobs:
                url = job.get("url", "")
                if not url:
                    continue
                resultado = _postular_empleo(page, url, user, job.get("titulo", ""))

                if resultado and isinstance(resultado, dict) and resultado.get("ok"):
                    bq.update_pending_job_estado(job["id"], "postulado")
                    bq.save_jobs([{
                        "id_empleo":         url,
                        "id_usuario":        user_id,
                        "titulo_empleo":     job.get("titulo", ""),
                        "cargo":             "",
                        "Fecha_Postulacion": datetime.datetime.utcnow().isoformat(),
                        "empresa":           job.get("empresa", ""),
                        "descripcion":       "",
                        "link":              url,
                        "portal":            "empleaxchile",
                    }])
                    ok_count += 1
                    print(f"    ✓ [pendientes-exc] {job.get('titulo','')[:50]}")
                else:
                    bq.update_pending_job_estado(job["id"], "fallido")
                    print(f"    ✗ [pendientes-exc] {job.get('titulo','')[:50]}")

                time.sleep(random.uniform(3, 6))

            bq.save_portal_cookies(user_id, "empleaxchile", ctx.cookies())
        finally:
            try:
                browser.close()
            except Exception:
                pass
            try:
                pw_obj.stop()
            except Exception:
                pass
    except Exception as e:
        import traceback
        print(f"  [pendientes-exc] Error {user_id}: {e}")
        traceback.print_exc()

    return ok_count


_PORTAL_HANDLERS = {
    "computrabajo": _postular_aprobados_cpt,
    "laborum":      _postular_aprobados_lab,
    "chiletrabajos": _postular_aprobados_cht,
    "empleaxchile":  _postular_aprobados_exc,
}


def _run():
    if sys.platform == "win32":
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    print("[postular_pendientes] Buscando empleos aprobados...")

    total_ok = 0

    for portal_name, handler in _PORTAL_HANDLERS.items():
        aprobados = bq.get_approved_pending_jobs(portal=portal_name)
        if not aprobados:
            print(f"  [{portal_name}] Sin empleos aprobados")
            continue

        # Agrupar por usuario
        by_user: dict[str, list] = defaultdict(list)
        for job in aprobados:
            by_user[job["id_usuario"]].append(job)

        print(f"  [{portal_name}] {len(aprobados)} empleos aprobados en {len(by_user)} usuarios")

        for user_id, jobs in by_user.items():
            usuarios = bq.get_user_by_id(user_id)
            if not usuarios:
                print(f"  [{portal_name}] Usuario {user_id} no encontrado — saltando")
                continue
            user = usuarios[0]
            n = handler(user_id, user, jobs)
            total_ok += n
            print(f"  [{portal_name}] {user_id}: {n}/{len(jobs)} postulados")

    print(f"\n[postular_pendientes] TOTAL: {total_ok} postulaciones de empleos aprobados")


if __name__ == "__main__":
    _run()
