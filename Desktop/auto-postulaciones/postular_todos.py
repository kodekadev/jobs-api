"""
Postulaciones automáticas en Trabajando.cl + ChileTrabajos para TODOS los usuarios.

Lógica de límites (igual que main.py):
  - Cada usuario tiene max_postulaciones_dia según su plan
  - Se divide en cuotas: Trabajando = ceil(max/2), ChileTrabajos = floor(max/2)
  - Si un portal no llena su cuota, el otro toma los slots restantes
  - Se descuentan las postulaciones ya hechas hoy antes de calcular cuotas

Un único reporte Telegram al finalizar el ciclo completo.
Ejecutar desde Spyder: seleccionar todo y Run Selection, o F5.
"""
import os, sys, threading, asyncio

# nest_asyncio permite que Playwright sync API funcione aunque BigQuery/gRPC
# haya iniciado un event loop asyncio en el mismo thread.
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

# En Windows, Playwright sync API no puede correr en el thread que ya tiene un
# loop asyncio activo (Spyder/IPython). La solución es ejecutar en un thread
# separado con WindowsProactorEventLoopPolicy (SelectorEventLoop no soporta
# subprocess en Windows).
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

def _run_pw(fn, *args, **kwargs):
    """Ejecuta fn en un thread separado con Playwright sync API.

    Python 3.12+ cambió asyncio._running_loop de threading.local a
    contextvars.ContextVar. Los threads heredan el contexto del padre, así que
    el thread hijo ve el loop de IPython/Spyder como "running". Playwright
    chequea loop.is_running() y lanza error.

    Solución: al inicio del thread, resetear _running_loop a None en el contexto
    del thread (no afecta al thread principal). Playwright verá RuntimeError →
    creará su propio loop fresco → is_running() False → procede normal.
    """
    result, exc = [None], [None]

    def _t():
        try:
            import asyncio.events as _ae
            import contextvars
            rl = getattr(_ae, '_running_loop', None)
            token = rl.set(None) if isinstance(rl, contextvars.ContextVar) else None
            try:
                result[0] = fn(*args, **kwargs)
            finally:
                if token is not None:
                    rl.reset(token)
        except Exception as e:
            exc[0] = e

    t = threading.Thread(target=_t)
    t.start()
    t.join()
    if exc[0]:
        raise exc[0]
    return result[0]

_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else r"C:\Users\bastian\Desktop\auto-postulaciones"
if _dir not in sys.path:
    sys.path.insert(0, _dir)

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")

from dotenv import load_dotenv
load_dotenv(os.path.join(_dir, ".env"))

# SOLO_USUARIO = "jobs_1783652775361_um90s"  # ej: "jobs2" para probar uno solo
# SOLO_USUARIO ="jobs234" #67a5ef61-88a9-44a4-ae05-69d70c21c73f" #para probar uno solo
SOLO_USUARIO =None#"jobs247" #67a5ef61-88a9-44a4-ae05-69d70c21c73f" #para probar uno solo

from plan_limits import get_plan_limit


def _get_max_dia(plan: str) -> int:
    return get_plan_limit(plan)


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
    _saved_pw = getattr(_pa, '_global_pw', None)  # preservar singleton entre reloads
    importlib.reload(_pa)
    if _saved_pw is not None:
        _pa._global_pw = _saved_pw
    from portal_accounts import get_trabajando_pw_session, apply_trabajando_playwright, job_aplica_al_usuario
    from scraper import _scrape_trabajando_playwright
    import chiletrabajos.postular as _cht_mod
    importlib.reload(_cht_mod)
    from chiletrabajos.postular import postular_empleos_cht
    from telegram_notify import enviar as telegram
    from trabajando_todos import _process_user
    import computrabajo.postular as _cpt_mod
    importlib.reload(_cpt_mod)
    from computrabajo.postular import postular_empleos_cpt
    from scraper import _scrape_computrabajo
    import laborum.postular as _lab_mod
    importlib.reload(_lab_mod)
    from laborum.postular import buscar_y_postular_lab
    import getonboard.postular as _gob_mod
    importlib.reload(_gob_mod)
    from getonboard.postular import postular_empleos_gob

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    print(f"[config] ANTHROPIC_API_KEY: {'OK (' + str(len(api_key)) + ' chars)' if api_key else 'FALTA'}")

    all_users = bq.get_active_users()
    if SOLO_USUARIO:
        all_users = [u for u in all_users if (u.get("ID_USUARIO") or u.get("id")) == SOLO_USUARIO]

    print(f"[main] {len(all_users)} usuario(s) a procesar\n")

    resultados = []  # acumular para el reporte final

    for i, user in enumerate(all_users, 1):
        uid    = user.get("ID_USUARIO") or user.get("id", "?")
        nombre = user.get("NOMBRE") or user.get("nombre") or uid
        plan   = user.get("PLAN")   or user.get("plan")   or "FREE"

        _ff = user.get("fecha_fin")
        if _ff is None:
            _vigencia = "sin vencimiento" if plan == "FREE" else "?"
        else:
            _ff_str = str(getattr(_ff, "value", _ff))[:10]
            _vigencia = f"vence {_ff_str}"

        max_dia = _get_max_dia(plan)

        # Status breve — se imprime para TODOS aunque salten
        print(f"\n[{i}/{len(all_users)}] {nombre} ({uid}) | {plan} | {max_dia}/día | {_vigencia}")

        # ── Límites del día ────────────────────────────────────────
        ya_hoy    = bq.get_postulaciones_hoy(uid)
        restantes = max(0, max_dia - ya_hoy)

        if restantes == 0:
            print(f"  → Límite diario alcanzado ({ya_hoy}/{max_dia}) — saltar")
            resultados.append({
                "nombre": nombre, "plan": plan, "max_dia": max_dia,
                "ya_hoy": ya_hoy, "tbj": 0, "cht": 0, "cpt": 0, "lab": 0, "gob": 0, "total_run": 0,
                "motivo": "limite_previo",
            })
            continue

        # Bloque detallado — solo cuando va a postular
        print(f"{'='*60}")
        print(f"  Postulaciones hoy: {ya_hoy}/{max_dia} | Restantes: {restantes}")
        print(f"{'='*60}")

        # ── Trabajando.cl ──────────────────────────────────────────
        tbj_ok = 0
        os.environ["PORTALES_ACTIVOS"] = "trabajando"
        try:
            res = _process_user(
                user, bq,
                get_trabajando_pw_session,
                apply_trabajando_playwright,
                _scrape_trabajando_playwright,
                max_count=restantes,
            )
            tbj_ok = res[0] if res else 0
        except Exception as e:
            print(f"  [ERROR Trabajando {uid}] {str(e).encode('ascii', 'replace').decode('ascii')}", flush=True)
        finally:
            # Nota: no cerramos el browser aquí — cerrar el browser del singleton
            # global de Playwright puede disparar os._exit(-1) = 255 en Windows y
            # matar el proceso antes de correr ChileTrabajos/Computrabajo/Laborum.
            # El browser se limpia solo cuando el proceso termina con os._exit(0).
            pass

        print(f"  [{uid}] Trabajando OK ({tbj_ok}) — continuando a ChileTrabajos", flush=True)
        # ChileTrabajos cubre los slots que Trabajando no llenó
        cuota_cht_final = max(0, restantes - tbj_ok)

        # ── ChileTrabajos ──────────────────────────────────────────
        cht_ok = 0
        os.environ["PORTALES_ACTIVOS"] = "chiletrabajos"
        cht_creds = bq.get_portal_account(uid, "chiletrabajos")
        if cht_creds and cuota_cht_final > 0:
            print(f"  [{uid}] Trabajando usó {tbj_ok}/{restantes} — ChileTrabajos cubre {cuota_cht_final} slots restantes", flush=True)
            try:
                cht_ok = postular_empleos_cht(uid, user, max_count=cuota_cht_final)
            except Exception as e:
                print(f"  [ERROR ChileTrabajos {uid}] {e}")
        elif not cht_creds and cuota_cht_final > 0:
            print(f"  [{uid}] Sin cuenta ChileTrabajos — {cuota_cht_final} slots sin usar hoy")

        # Computrabajo cubre los slots que Trabajando + ChileTrabajos no llenaron
        cuota_cpt_final = max(0, restantes - tbj_ok - cht_ok)

        # ── Computrabajo ───────────────────────────────────────────────────────
        cpt_ok = 0
        os.environ["PORTALES_ACTIVOS"] = "computrabajo"
        cpt_creds = bq.get_portal_account(uid, "computrabajo")
        if cpt_creds and cuota_cpt_final > 0:
            print(f"  [{uid}] Trabajando+ChileTrabajos usaron {tbj_ok+cht_ok}/{restantes} — Computrabajo cubre {cuota_cpt_final} slots", flush=True)
            try:
                cpt_ok = postular_empleos_cpt(uid, user, max_n=cuota_cpt_final)
            except Exception as e:
                print(f"  [ERROR Computrabajo {uid}] {e}")
        elif not cpt_creds and cuota_cpt_final > 0:
            print(f"  [{uid}] Sin cuenta Computrabajo — {cuota_cpt_final} slots sin usar hoy")

        # Laborum cubre los slots que los otros portales no llenaron
        cuota_lab_final = max(0, restantes - tbj_ok - cht_ok - cpt_ok)

        # ── Laborum ────────────────────────────────────────────────────────────
        lab_ok = 0
        os.environ["PORTALES_ACTIVOS"] = "laborum"
        lab_creds = bq.get_portal_account(uid, "laborum")
        if lab_creds and cuota_lab_final > 0:
            print(f"  [{uid}] Trabajando+ChileTrabajos+Computrabajo usaron {tbj_ok+cht_ok+cpt_ok}/{restantes} — Laborum cubre {cuota_lab_final} slots")
            try:
                import json as _json
                cargos = user.get("CARGOS") or user.get("cargos") or []
                if isinstance(cargos, str):
                    try: cargos = _json.loads(cargos)
                    except Exception: cargos = [cargos]
                ubicaciones = user.get("UBICACIONES") or user.get("ubicaciones") or ["Santiago"]
                if isinstance(ubicaciones, str):
                    try: ubicaciones = _json.loads(ubicaciones)
                    except Exception: ubicaciones = [ubicaciones]
                ubicacion = (ubicaciones[0] if ubicaciones else "Santiago").split(",")[0].strip()
                lab_ok = buscar_y_postular_lab(uid, user, cargos if cargos else [""], ubicacion, max_n=cuota_lab_final)
            except Exception as e:
                print(f"  [ERROR Laborum {uid}] {e}")
        elif not lab_creds and cuota_lab_final > 0:
            print(f"  [{uid}] Sin cuenta Laborum — {cuota_lab_final} slots sin usar hoy")

        # GetOnBoard cubre los slots que todos los demás portales no llenaron
        cuota_gob_final = max(0, restantes - tbj_ok - cht_ok - cpt_ok - lab_ok)

        # ── GetOnBoard ─────────────────────────────────────────────────────────
        gob_ok = 0
        os.environ["PORTALES_ACTIVOS"] = "getonboard"
        gob_creds = bq.get_portal_account(uid, "getonboard")
        if gob_creds and cuota_gob_final > 0:
            print(f"  [{uid}] Tbj+Cht+Cpt+Lab usaron {tbj_ok+cht_ok+cpt_ok+lab_ok}/{restantes} — GetOnBoard cubre {cuota_gob_final} slots")
            try:
                gob_ok = postular_empleos_gob(uid, user, max_n=cuota_gob_final)
            except Exception as e:
                print(f"  [ERROR GetOnBoard {uid}] {e}")
        elif not gob_creds and cuota_gob_final > 0:
            print(f"  [{uid}] Sin cuenta GetOnBoard — {cuota_gob_final} slots sin usar hoy")

        total_user = tbj_ok + cht_ok + cpt_ok + lab_ok + gob_ok
        print(f"\n  [{uid}] TOTAL: {total_user} postulaciones (Trabajando: {tbj_ok} | ChileTrabajos: {cht_ok} | Computrabajo: {cpt_ok} | Laborum: {lab_ok} | GetOnBoard: {gob_ok})")

        # Cerrar browser ChileTrabajos (Trabajando ya se cerró después de su bloque)
        try:
            _pa._close_cht_pw_session(uid)
        except Exception:
            pass

        motivo = None
        if total_user == 0:
            motivo = "sin_ofertas"
        elif total_user < restantes:
            motivo = "parcial"

        # Email de resumen al usuario (portales consultados desde BQ)
        try:
            from notifier import send_summary as _send_summary
            _send_summary(user, [], [])
        except Exception as _e:
            print(f"  [{uid}] Error enviando resumen email: {_e}")

        resultados.append({
            "nombre": nombre, "plan": plan, "max_dia": max_dia,
            "ya_hoy": ya_hoy, "tbj": tbj_ok, "cht": cht_ok, "cpt": cpt_ok, "lab": lab_ok, "gob": gob_ok,
            "total_run": total_user, "motivo": motivo,
        })

    print(f"\n{'='*60}")
    print(f"Proceso finalizado — {len(all_users)} usuario(s)")

    # ── Reporte Telegram único ─────────────────────────────────────────────────
    from datetime import date as _date
    hoy = _date.today().strftime("%d/%m")
    total_postulaciones = sum(r["ya_hoy"] + r["total_run"] for r in resultados)
    n_usuarios = len(resultados)

    completaron   = [r for r in resultados if r["ya_hoy"] + r["total_run"] >= r["max_dia"]]
    parciales     = [r for r in resultados if r["motivo"] == "parcial"]
    sin_ofertas   = [r for r in resultados if r["motivo"] == "sin_ofertas"]
    limite_previo = [r for r in resultados if r["motivo"] == "limite_previo"]

    sin_ninguna = [r for r in resultados if r["ya_hoy"] + r["total_run"] == 0]

    total_tbj = sum(r.get("tbj", 0) for r in resultados)
    total_cht = sum(r.get("cht", 0) for r in resultados)
    total_cpt = sum(r.get("cpt", 0) for r in resultados)
    total_lab = sum(r.get("lab", 0) for r in resultados)
    total_gob = sum(r.get("gob", 0) for r in resultados)

    msg = (
        f"[AplicAI] {hoy}\n"
        f"{n_usuarios} usuarios | {total_postulaciones} postulaciones hoy\n"
        f"Trabajando: {total_tbj} | ChileTrabajos: {total_cht} | Computrabajo: {total_cpt} | Laborum: {total_lab} | GetOnBoard: {total_gob}\n\n"
        f"[OK] Completaron limite: {len(completaron)}\n"
        f"[~]  Parcial (no completaron): {len(parciales)}\n"
        f"[?]  Sin ofertas disponibles: {len(sin_ofertas)}\n"
        f"[>>] Limite ya alcanzado: {len(limite_previo)}\n"
        f"[-]  Sin ninguna postulacion hoy: {len(sin_ninguna)}"
    )
    telegram(msg)


if __name__ == "__main__":
    # UTF-8 ya se garantiza con PYTHONIOENCODING=utf-8 en Jenkins.
    # En Spyder sys.stdout es TTYOutStream (no TextIOWrapper) — no hacer nada.
    import io
    if isinstance(sys.stdout, io.TextIOWrapper):
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        except Exception:
            pass
    if isinstance(sys.stderr, io.TextIOWrapper):
        try:
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        except Exception:
            pass

    # os._exit bypasea el cleanup de Playwright (que devuelve exit 255 en Windows).
    # Usamos SPYDER_ARGS / JPY_SESSION_NAME para detectar entorno interactivo;
    # NO usamos IPython/spyder en sys.modules porque Anaconda los importa en CLI también.
    _in_interactive = (
        bool(os.environ.get("SPYDER_ARGS")) or
        bool(os.environ.get("JPY_SESSION_NAME")) or
        bool(os.environ.get("APLICAI_SOFT_EXIT"))
    )
    print(f"[exit] interactive={_in_interactive} | SPYDER_ARGS={os.environ.get('SPYDER_ARGS')!r} | JPY={os.environ.get('JPY_SESSION_NAME')!r}")

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
        print(f"[FATAL] {exc[0]}")
        import traceback; traceback.print_exception(type(exc[0]), exc[0], exc[0].__traceback__)
        if not _in_interactive:
            os._exit(1)
    elif not _in_interactive:
        os._exit(0)  # Bypass Playwright async cleanup que devuelve exit 255
