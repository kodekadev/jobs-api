"""
Postulaciones automáticas para UN portal, todos los usuarios activos.

Uso:
    python postular_portal.py trabajando
    python postular_portal.py chiletrabajos
    python postular_portal.py computrabajo
    python postular_portal.py laborum

Jenkins Pipeline (4 stages secuenciales):
    Stage 1: python postular_portal.py trabajando
    Stage 2: python postular_portal.py chiletrabajos
    Stage 3: python postular_portal.py computrabajo
    Stage 4: python postular_portal.py laborum

Cada stage lee get_postulaciones_hoy(uid) de BigQuery al inicio de cada usuario,
lo que incluye todo lo que stages anteriores ya registraron.
→ La cuota diaria se respeta automáticamente sin coordinación extra.
"""
import os
import sys
import io
import threading
import asyncio

# ── Encoding ──────────────────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
elif isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# ── Paths y env ───────────────────────────────────────────────────────────────
_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else r"C:\Users\bastian\Desktop\auto-postulaciones"
if _dir not in sys.path:
    sys.path.insert(0, _dir)

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")

from dotenv import load_dotenv
load_dotenv(os.path.join(_dir, ".env"))

# ── Portal ────────────────────────────────────────────────────────────────────
PORTALES_VALIDOS = ("trabajando", "chiletrabajos", "computrabajo", "laborum",
                    "empleaxchile")

if len(sys.argv) < 2 or sys.argv[1].lower() not in PORTALES_VALIDOS:
    print(f"Uso: python postular_portal.py <portal>")
    print(f"Portales válidos: {', '.join(PORTALES_VALIDOS)}")
    sys.exit(1)

PORTAL = sys.argv[1].lower()
os.environ["PORTALES_ACTIVOS"] = PORTAL

# ── Testing desde Spyder: limitar a un usuario específico ─────────────────────
# Cambiar a None para procesar todos los usuarios activos
SOLO_USUARIO: "str | None" = None   # ej: "jobs2"

# ── Cuotas ────────────────────────────────────────────────────────────────────
# Fuente única: plan_limits.py
from plan_limits import get_daily_limit

# ── _run_pw: aísla Playwright sync en thread limpio ───────────────────────────
def _run_pw(fn, *args, **kwargs):
    """Ejecuta fn en un thread con loop asyncio propio (evita conflicto con gRPC/BQ)."""
    result, exc = [None], [None]

    def _t():
        try:
            import asyncio.events as _ae, contextvars
            rl = getattr(_ae, "_running_loop", None)
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


# ── Lógica principal ──────────────────────────────────────────────────────────
def _run():
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        import nest_asyncio
        nest_asyncio.apply()
    except ImportError:
        pass

    import importlib
    import bq

    # Imports por portal
    if PORTAL == "trabajando":
        import portal_accounts as _pa
        _saved_pw = getattr(_pa, "_global_pw", None)
        importlib.reload(_pa)
        if _saved_pw is not None:
            _pa._global_pw = _saved_pw
        from portal_accounts import get_trabajando_pw_session, apply_trabajando_playwright
        from scraper import _scrape_trabajando_playwright
        from trabajando_todos import _process_user

    elif PORTAL == "chiletrabajos":
        import chiletrabajos.postular as _cht_mod
        importlib.reload(_cht_mod)
        from chiletrabajos.postular import postular_empleos_cht

    elif PORTAL == "computrabajo":
        import computrabajo.postular as _cpt_mod
        importlib.reload(_cpt_mod)
        from computrabajo.postular import postular_empleos_cpt

    elif PORTAL == "laborum":
        import laborum.postular as _lab_mod
        importlib.reload(_lab_mod)
        from laborum.postular import buscar_y_postular_lab

    elif PORTAL == "empleaxchile":
        import empleaxchile.postular as _exc_mod
        importlib.reload(_exc_mod)
        from empleaxchile.postular import postular_empleos_exc

    all_users = bq.get_active_users()
    if SOLO_USUARIO:
        all_users = [u for u in all_users if u.get("ID_USUARIO") == SOLO_USUARIO]
    print(f"[{PORTAL}] {len(all_users)} usuario(s) a procesar\n")

    resultados = []

    for user in all_users:
        uid    = user.get("ID_USUARIO") or user.get("id", "?")
        nombre = user.get("NOMBRE") or user.get("nombre") or uid
        plan   = user.get("PLAN") or user.get("plan") or "FREE"

        print(f"\n{'='*60}")
        print(f"  [{PORTAL}] {nombre} ({uid}) | Plan: {plan}")
        print(f"{'='*60}")

        # ── Crear cuenta si no existe ──────────────────────────────────────────
        cuenta = bq.get_portal_account(uid, PORTAL)
        if not cuenta:
            print(f"  [{uid}] Sin cuenta {PORTAL} — creando...")
            try:
                if PORTAL == "trabajando":
                    from trabajando.crear_cuenta import crear_cuenta_trabajando as _crear
                    if _crear(uid, user):
                        from trabajando.completar_perfil import completar_perfil_trabajando as _cv
                        _cv(uid, user)
                        cuenta = bq.get_portal_account(uid, PORTAL)
                elif PORTAL == "chiletrabajos":
                    from chiletrabajos.crear_cuenta import crear_cuenta_chiletrabajos as _crear
                    if _crear(uid, user):
                        from chiletrabajos.completar_perfil import _pw_completar_perfil_chiletrabajos as _cv
                        _cv(uid, user)
                        cuenta = bq.get_portal_account(uid, PORTAL)
                elif PORTAL == "computrabajo":
                    from computrabajo.crear_cuenta import crear_cuenta_computrabajo as _crear
                    if _crear(uid, user):
                        from computrabajo.completar_perfil import completar_perfil_computrabajo as _cv
                        _cv(uid, user)
                        cuenta = bq.get_portal_account(uid, PORTAL)
                elif PORTAL == "empleaxchile":
                    from empleaxchile.crear_cuenta import crear_cuenta_empleaxchile as _crear
                    if _crear(uid, user):
                        from empleaxchile.completar_perfil import completar_perfil_empleaxchile as _cv
                        _cv(uid, user)
                        cuenta = bq.get_portal_account(uid, PORTAL)
                elif PORTAL == "laborum":
                    from laborum.crear_cuenta import crear_cuenta_laborum as _crear
                    if _crear(uid, user):
                        from laborum.completar_perfil import completar_perfil_laborum as _cv
                        _cv(uid, user)
                        cuenta = bq.get_portal_account(uid, PORTAL)
            except Exception as e:
                print(f"  [{uid}] Setup {PORTAL} error: {e}")

        if not cuenta:
            print(f"  [{uid}] Sin cuenta {PORTAL} — saltar")
            resultados.append({"nombre": nombre, "plan": plan, "ok": 0, "motivo": "sin_cuenta"})
            continue

        # ── Cuota: leer BQ en este momento (incluye lo que otros stages ya registraron) ──
        max_dia   = get_daily_limit(user)
        ya_hoy    = bq.get_postulaciones_hoy(uid)
        restantes = max(0, max_dia - ya_hoy)

        if restantes == 0:
            print(f"  [{uid}] Límite diario alcanzado ({ya_hoy}/{max_dia}) — saltar")
            resultados.append({"nombre": nombre, "plan": plan, "ok": 0, "motivo": "limite_alcanzado"})
            continue

        print(f"  [{uid}] Postulaciones hoy: {ya_hoy}/{max_dia} | Restantes para {PORTAL}: {restantes}")

        # ── Postular ───────────────────────────────────────────────────────────
        ok = 0
        try:
            if PORTAL == "trabajando":
                import portal_accounts as _pa
                res = _process_user(
                    user, bq,
                    get_trabajando_pw_session,
                    apply_trabajando_playwright,
                    _scrape_trabajando_playwright,
                    max_count=restantes,
                )
                ok = res[0] if res else 0
                # Cerrar browser inmediatamente tras terminar
                try:
                    _pa._close_pw_session(uid)
                except Exception:
                    pass

            elif PORTAL == "chiletrabajos":
                ok = postular_empleos_cht(uid, user, max_count=restantes)

            elif PORTAL == "computrabajo":
                ok = _run_pw(postular_empleos_cpt, uid, user, max_n=restantes)

            elif PORTAL == "empleaxchile":
                ok = postular_empleos_exc(uid, user, max_count=restantes)

            elif PORTAL == "laborum":
                import json as _json
                cargos = user.get("CARGOS") or user.get("cargos") or []
                if isinstance(cargos, str):
                    try:
                        cargos = _json.loads(cargos)
                    except Exception:
                        cargos = [cargos]
                ubicaciones = user.get("UBICACIONES") or user.get("ubicaciones") or ["Santiago"]
                if isinstance(ubicaciones, str):
                    try:
                        ubicaciones = _json.loads(ubicaciones)
                    except Exception:
                        ubicaciones = [ubicaciones]
                ubicacion = (ubicaciones[0] if ubicaciones else "Santiago").split(",")[0].strip()
                ok = _run_pw(buscar_y_postular_lab, uid, user, cargos if cargos else [""], ubicacion, max_n=restantes)

        except Exception as e:
            print(f"  [ERROR {PORTAL} {uid}] {str(e).encode('ascii', 'replace').decode('ascii')}")

        # Cerrar sesión ChileTrabajos si aplica
        if PORTAL == "chiletrabajos":
            try:
                import portal_accounts as _pa
                _pa._close_cht_pw_session(uid)
            except Exception:
                pass

        motivo = None if ok > 0 else ("sin_ofertas" if restantes > 0 else "limite_alcanzado")
        print(f"  [{uid}] {PORTAL}: {ok} postulaciones")
        resultados.append({"nombre": nombre, "plan": plan, "ok": ok, "motivo": motivo})

        # Métrica del día. Los portales que aún no cuentan ofertas compatibles
        # reportan compatibles=postuladas y 0 perdidas, que es lo honesto:
        # no sabemos si hubo más.
        try:
            import metricas_diarias
            _m = {"compatibles": ok, "perdidas_limite": 0}
            if PORTAL == "chiletrabajos":
                from chiletrabajos.postular import ULTIMA_CORRIDA as _cht_m
                _m = dict(_cht_m)
            metricas_diarias.registrar(
                id_usuario=uid, portal=PORTAL, plan=plan, limite_dia=max_dia,
                ofertas_compatibles=_m.get("compatibles", ok),
                postuladas=ok,
                no_postuladas_limite=_m.get("perdidas_limite", 0),
            )
        except Exception as _me:
            print(f"  [{uid}] metricas no registradas: {_me}")

        # Email cuando usuario TRIAL alcanza el límite diario en este stage
        if ok > 0 and plan.upper() == "TRIAL" and ya_hoy < max_dia and ya_hoy + ok >= max_dia:
            try:
                import notifier as _notifier
                total_hoy = ya_hoy + ok
                _notifier.send_trial_daily_limit(user, total_hoy)
            except Exception as _e:
                print(f"  ⚠ Error enviando email límite trial [{uid}]: {_e}")

    # ── Reporte Telegram ───────────────────────────────────────────────────────
    from datetime import date as _date
    from telegram_notify import enviar as telegram

    hoy = _date.today().strftime("%d/%m")
    total_ok       = sum(r["ok"] for r in resultados)
    completaron    = [r for r in resultados if r["ok"] > 0 and r["motivo"] is None]
    sin_ofertas    = [r for r in resultados if r["motivo"] == "sin_ofertas"]
    limite_previo  = [r for r in resultados if r["motivo"] == "limite_alcanzado"]
    sin_cuenta     = [r for r in resultados if r["motivo"] == "sin_cuenta"]

    portal_label = {
        "trabajando":   "Trabajando.cl",
        "chiletrabajos": "ChileTrabajos",
        "computrabajo": "Computrabajo",
        "laborum":      "Laborum",
    }[PORTAL]

    msg = (
        f"[AplicAI] {portal_label} | {hoy}\n"
        f"{len(resultados)} usuarios | {total_ok} postulaciones\n\n"
        f"[OK] Con postulaciones: {len(completaron)}\n"
        f"[?]  Sin ofertas disponibles: {len(sin_ofertas)}\n"
        f"[>>] Límite ya alcanzado: {len(limite_previo)}\n"
        f"[-]  Sin cuenta en portal: {len(sin_cuenta)}"
    )
    telegram(msg)
    print(f"\n[{PORTAL}] Fin — {total_ok} postulaciones en {len(resultados)} usuarios")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"[{PORTAL}] Iniciando...")

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
        import traceback
        print(f"[FATAL] {exc[0]}")
        traceback.print_exception(type(exc[0]), exc[0], exc[0].__traceback__)
        os._exit(1)
    else:
        os._exit(0)
