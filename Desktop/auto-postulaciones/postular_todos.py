"""
Postulaciones automáticas en Trabajando.cl + ChileTrabajos para TODOS los usuarios.

Lógica de límites (igual que main.py):
  - Cada usuario tiene max_postulaciones_dia según su plan
  - Se divide en cuotas: Trabajando = ceil(max/2), ChileTrabajos = floor(max/2)
  - Si un portal no llena su cuota, el otro toma los slots restantes
  - Se descuentan las postulaciones ya hechas hoy antes de calcular cuotas

Telegram consolidado por usuario al final.
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

_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else r"C:\Users\bastian\Desktop\auto-postulaciones"
if _dir not in sys.path:
    sys.path.insert(0, _dir)

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")

from dotenv import load_dotenv
load_dotenv(os.path.join(_dir, ".env"))

# SOLO_USUARIO = "jobs_1783652775361_um90s"  # ej: "jobs2" para probar uno solo
SOLO_USUARIO = None  # ej: "jobs2" para probar uno solo

PLAN_LIMITS = {
    "FREE":    {"max_postulaciones_dia": 5},
    "PRO":     {"max_postulaciones_dia": 25},
    "SPRINT":  {"max_postulaciones_dia": 40},
    "PREMIUM": {"max_postulaciones_dia": 50},
    "TRIAL":   {"max_postulaciones_dia": 25},
}


def _get_max_dia(plan: str) -> int:
    return PLAN_LIMITS.get((plan or "FREE").upper(), PLAN_LIMITS["FREE"])["max_postulaciones_dia"]


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
    import chiletrabajos.postular as _cht_mod
    importlib.reload(_cht_mod)
    from chiletrabajos.postular import postular_empleos_cht
    from telegram_notify import enviar as telegram
    from trabajando_todos import _process_user

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    print(f"[config] ANTHROPIC_API_KEY: {'OK (' + str(len(api_key)) + ' chars)' if api_key else 'FALTA'}")

    all_users = bq.get_active_users()
    if SOLO_USUARIO:
        all_users = [u for u in all_users if (u.get("ID_USUARIO") or u.get("id")) == SOLO_USUARIO]

    print(f"[main] {len(all_users)} usuario(s) a procesar\n")

    for user in all_users:
        uid    = user.get("ID_USUARIO") or user.get("id", "?")
        nombre = user.get("NOMBRE") or user.get("nombre") or uid
        plan   = user.get("PLAN")   or user.get("plan")   or "FREE"

        print(f"\n{'='*60}")
        print(f"  USUARIO: {nombre} ({uid}) | Plan: {plan}")
        print(f"{'='*60}")

        # ── Setup: crear cuentas y llenar CV si faltan (Selenium) ─────────────
        if not bq.get_portal_account(uid, "trabajando"):
            print(f"  [{uid}] Sin cuenta Trabajando — creando y llenando CV...")
            try:
                from trabajando.crear_cuenta import crear_cuenta_trabajando as _crear_tbj
                if _crear_tbj(uid, user):
                    from trabajando.completar_perfil import completar_perfil_trabajando as _cv_tbj
                    _cv_tbj(uid, user)
            except Exception as e:
                print(f"  [{uid}] Setup Trabajando error: {e}")

        if not bq.get_portal_account(uid, "chiletrabajos"):
            print(f"  [{uid}] Sin cuenta ChileTrabajos — creando y llenando CV...")
            try:
                from chiletrabajos.crear_cuenta import crear_cuenta_chiletrabajos as _crear_cht
                if _crear_cht(uid, user):
                    from chiletrabajos.completar_perfil import _pw_completar_perfil_chiletrabajos as _cv_cht
                    _cv_cht(uid, user)
            except Exception as e:
                print(f"  [{uid}] Setup ChileTrabajos error: {e}")

        # ── Límites del día ────────────────────────────────────────
        max_dia    = _get_max_dia(plan)
        ya_hoy     = bq.get_postulaciones_hoy(uid)
        restantes  = max(0, max_dia - ya_hoy)

        if restantes == 0:
            print(f"  [{uid}] Limite diario alcanzado ({ya_hoy}/{max_dia}) a saltar")
            telegram(
                f"[AplicAI] 0 postulaciones\n"
                f"Usuario: {nombre} | Plan: {plan}\n"
                f"Limite diario ya alcanzado ({ya_hoy}/{max_dia})"
            )
            continue

        # Trabajando recibe el cupo completo; ChileTrabajos cubre lo que quede sin llenar
        print(f"  [{uid}] Postulaciones hoy: {ya_hoy}/{max_dia} | Restantes: {restantes}")

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
            print(f"  [ERROR Trabajando {uid}] {str(e).encode('ascii', 'replace').decode('ascii')}")

        # ChileTrabajos cubre los slots que Trabajando no llenó
        cuota_cht_final = max(0, restantes - tbj_ok)

        # ── ChileTrabajos ──────────────────────────────────────────
        cht_ok = 0
        os.environ["PORTALES_ACTIVOS"] = "chiletrabajos"
        cht_creds = bq.get_portal_account(uid, "chiletrabajos")
        if cht_creds and cuota_cht_final > 0:
            print(f"  [{uid}] Trabajando usó {tbj_ok}/{restantes} — ChileTrabajos cubre {cuota_cht_final} slots restantes")
            try:
                cht_ok = postular_empleos_cht(uid, user, max_count=cuota_cht_final)
            except Exception as e:
                print(f"  [ERROR ChileTrabajos {uid}] {e}")
        elif not cht_creds and cuota_cht_final > 0:
            print(f"  [{uid}] Sin cuenta ChileTrabajos — {cuota_cht_final} slots sin usar hoy")

        cht_sobrante = max(0, cuota_cht_final - cht_ok)
        if cht_sobrante and cht_ok < cuota_cht_final:
            print(f"  [{uid}] ChileTrabajos usó {cht_ok}/{cuota_cht_final} — {cht_sobrante} slots sin usar hoy")

        total_user = tbj_ok + cht_ok
        print(f"\n  [{uid}] TOTAL: {total_user} postulaciones (Trabajando: {tbj_ok} | ChileTrabajos: {cht_ok})")

        # Telegram consolidado
        telegram(
            f"[AplicAI] {total_user} postulaciones enviadas\n"
            f"Usuario: {nombre} | Plan: {plan}\n"
            f"Canales: Trabajando {tbj_ok}/{cuota_tbj} | ChileTrabajos {cht_ok}/{cuota_cht_final} | Limite: {max_dia}/dia"
        )

    print(f"\n{'='*60}")
    print(f"Proceso finalizado — {len(all_users)} usuario(s)")


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
