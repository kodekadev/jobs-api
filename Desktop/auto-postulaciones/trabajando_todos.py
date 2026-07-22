"""
Postulaciones automáticas en Trabajando.cl para TODOS los usuarios activos.
Ejecutar desde Spyder: seleccionar todo y Run Selection, o F5.

Para un usuario específico, cambiar SOLO_USUARIO = "jobs2" (o None para todos).
"""
import os, sys, threading, asyncio
from emailer import extract_email, send_application
from telegram_notify import enviar as telegram

_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else r"C:\Users\bastian\Desktop\auto-postulaciones"
if _dir not in sys.path:
    sys.path.insert(0, _dir)

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")
os.environ["PORTALES_ACTIVOS"] = "trabajando"

from dotenv import load_dotenv
load_dotenv(os.path.join(_dir, ".env"))

# None = todos los usuarios activos
SOLO_USUARIO = None  # ej: "jobs2" para probar uno solo


def _process_user(user: dict, bq, get_trabajando_pw_session, apply_trabajando_playwright, _scrape_trabajando_playwright, max_count: int = 999):
    """Procesa un usuario: busca empleos por cada cargo y postula."""
    import time, uuid
    from datetime import datetime, timezone
    from portal_accounts import job_aplica_al_usuario

    uid     = user.get("ID_USUARIO") or user.get("id", "?")
    nombre  = user.get("NOMBRE") or user.get("nombre") or uid
    cargos  = user.get("CARGOS") or user.get("cargos") or []
    ubicaciones = user.get("UBICACIONES") or user.get("ubicaciones") or []

    if isinstance(cargos, str):
        import json
        try: cargos = json.loads(cargos)
        except: cargos = [cargos]
    if isinstance(ubicaciones, str):
        import json
        try: ubicaciones = json.loads(ubicaciones)
        except: ubicaciones = [ubicaciones]

    if not cargos:
        print(f"  [{uid}] Sin cargos configurados — saltar")
        return

    # Ubicación principal para la búsqueda (primera o "santiago" por defecto)
    ubicacion = (ubicaciones[0] if ubicaciones else "santiago").strip()

    creds = bq.get_portal_account(uid, "trabajando")
    if not creds:
        print(f"  [{uid}] Sin cuenta Trabajando.cl — saltar")
        return

    print(f"\n{'='*60}")
    print(f"  USUARIO: {nombre} ({uid})")
    print(f"  Cargos:  {', '.join(cargos)}")
    print(f"  Ciudad:  {ubicacion}")
    print(f"{'='*60}")

    # IDs ya postulados (para no duplicar en BQ ni reintentar)
    ya_postulados: set = set()
    try:
        ya_postulados = bq.get_applied_job_ids(uid)
        print(f"  [{uid}] {len(ya_postulados)} postulaciones previas en BQ")
    except Exception as e:
        print(f"  [{uid}] No se pudo cargar historial: {e}")

    # Iniciar sesión
    print(f"  [{uid}] Iniciando sesión Trabajando.cl...")
    page = get_trabajando_pw_session(uid, creds["email"], creds["password"])
    if not page:
        import portal_accounts as _pa_mod
        motivo_login = _pa_mod._portal_login_failures.get(creds.get("email", ""), "desconocido")
        print(f"  [{uid}] ERROR: No se pudo iniciar sesión — MOTIVO: {motivo_login}")
        telegram(
            f"[AplicAI] Fallo sesión Trabajando.cl\n"
            f"Usuario: {nombre} ({uid})\n"
            f"Email portal: {creds.get('email','?')}\n"
            f"Motivo: {motivo_login}"
        )
        return

    ok_count = err_count = 0
    applied_jobs: list[dict] = []

    for cargo in cargos:
        print(f"\n  [{uid}] Buscando '{cargo}' en '{ubicacion}'...")
        try:
            jobs = _scrape_trabajando_playwright(page, cargo, ubicacion, n=200)
        except Exception as e:
            print(f"  [{uid}] Error scraping '{cargo}': {e}")
            continue

        # Filtrar ya postulados
        nuevos = [j for j in jobs if (j.get('link') or j.get('id', '')) not in ya_postulados]
        print(f"  [{uid}] {len(jobs)} encontrados, {len(nuevos)} nuevos para '{cargo}'")

        for i, j in enumerate(nuevos, 1):
            titulo = j['titulo']
            empresa_scrape = j.get('empresa', '')
            url    = j.get('link') or j.get('id', '')

            # Filtrar empleos que no aplican al perfil del usuario
            aplica, motivo = job_aplica_al_usuario(titulo, empresa_scrape, user)
            if not aplica:
                print(f"\n    {i:>3}/{len(nuevos)}. SALTADO ({motivo}): '{titulo}'")
                continue

            print(f"\n    {i:>3}/{len(nuevos)}. '{titulo}' — {empresa_scrape}")
            print(f"         {url}")

            result = None
            try:
                result = apply_trabajando_playwright(page, url, user=user, job_title=titulo)
            except Exception as e:
                if "TargetClosedError" in type(e).__name__ or "closed" in str(e).lower():
                    print(f"         [RECONECTANDO] Browser cerrado, reconectando...")
                    try:
                        page = get_trabajando_pw_session(uid, creds["email"], creds["password"])
                        print(f"         [RECONECTADO] Nueva sesión OK — reintentando...")
                        result = apply_trabajando_playwright(page, url, user=user, job_title=titulo)
                    except Exception as re_e:
                        print(f"         [ERROR RECONEXION] {re_e}")
                        err_count += 1
                        time.sleep(1.5)
                        continue
                else:
                    print(f"         [ERR] {e}")
                    err_count += 1
                    time.sleep(1.5)
                    continue

            if result and isinstance(result, dict):
                exito       = result.get("ok") or result.get("exito") or result.get("success")
                ya_aplicado = result.get("ya_postulado", False)
            else:
                exito       = bool(result)
                ya_aplicado = False

            if exito:
                ya_postulados.add(url)
                if ok_count >= max_count:
                    print(f"         [LÍMITE] {max_count} alcanzado — deteniendo Trabajando")
                    return ok_count, err_count
                if ya_aplicado:
                    print(f"         — Ya postulado (saltando)")
                    time.sleep(0.3)
                    continue
                print(f"         ✓ POSTULADO")
                ok_count += 1
                applied_jobs.append({"titulo": titulo, "empresa": empresa_scrape})

                empresa_final     = (isinstance(result, dict) and result.get("empresa")) or empresa_scrape or ""
                descripcion_final = (isinstance(result, dict) and result.get("descripcion")) or ""

                # Enviar email al reclutador si hay email en la descripción
                email_rec = extract_email(descripcion_final)
                if email_rec:
                    if bq.ya_envio_email(uid, email_rec):
                        print(f"         [email] Ya enviado a {email_rec} — skip")
                    else:
                        if send_application(user, {"titulo": titulo, "empresa": empresa_final}, email_rec):
                            print(f"         [email] Enviado a {email_rec} ✓")
                            descripcion_final += f"\n\n[email_directo: {email_rec}]"

                try:
                    bq.save_jobs([{
                        "id_empleo":         url or str(uuid.uuid4()),
                        "id_usuario":        uid,
                        "titulo_empleo":     titulo,
                        "cargo":             cargo,
                        "Fecha_Postulacion": datetime.now(timezone.utc).isoformat(),
                        "empresa":           empresa_final,
                        "descripcion":       descripcion_final,
                        "link":              url,
                        "portal":            "trabajando",
                    }])
                    print(f"         [BQ] Guardado OK")
                except Exception as bq_e:
                    print(f"         [BQ] Error: {bq_e}")
            else:
                print(f"         ✗ Sin postular")
                err_count += 1

            time.sleep(1.5)

    print(f"\n  [{uid}] Fin: {ok_count} postulados / {err_count} sin postular")

    return ok_count, err_count


def _run():
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        import nest_asyncio
        nest_asyncio.apply(loop)
    except ImportError:
        pass

    import importlib
    import bq
    import portal_accounts as _pa
    importlib.reload(_pa)
    from portal_accounts import get_trabajando_pw_session, apply_trabajando_playwright, job_aplica_al_usuario
    from scraper import _scrape_trabajando_playwright

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    print(f"[config] ANTHROPIC_API_KEY: {'OK (' + str(len(api_key)) + ' chars)' if api_key else 'FALTA'}")

    all_users = bq.get_active_users()
    if SOLO_USUARIO:
        all_users = [u for u in all_users if u["ID_USUARIO"] == SOLO_USUARIO]

    print(f"[main] {len(all_users)} usuario(s) a procesar")

    total_ok = total_err = 0
    for user in all_users:
        try:
            res = _process_user(
                user, bq,
                get_trabajando_pw_session,
                apply_trabajando_playwright,
                _scrape_trabajando_playwright,
            )
            if res:
                total_ok  += res[0]
                total_err += res[1]
        except Exception as e:
            print(f"  [ERROR usuario {user['ID_USUARIO']}] {e}")

    print(f"\n{'='*60}")
    print(f"TOTAL: {total_ok} postulados / {total_err} sin postular / {len(all_users)} usuarios")


if __name__ == "__main__":
    exc = [None]
    def _thread():
        try:
            _run()
        except Exception as e:
            exc[0] = e

    t = threading.Thread(target=_thread)
    t.start()
    t.join()
    if exc[0]:
        raise exc[0]
