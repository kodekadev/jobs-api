"""
Trabajando.cl — postulaciones respetando cupo diario global del usuario.

Consulta cuántas postulaciones lleva el usuario HOY (en todos los portales),
calcula el cupo restante y solo postula hasta ese límite.
Si otro portal ya agotó el cupo, este script no hace nada para ese usuario.

Uso desde Spyder: seleccionar todo y Run Selection, o F5.
Para un usuario específico: SOLO_USUARIO = "jobs2"
"""
import os
import sys
import threading
import asyncio

_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else r"C:\Users\bastian\Desktop\auto-postulaciones"
if _dir not in sys.path:
    sys.path.insert(0, _dir)

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")
os.environ["PORTALES_ACTIVOS"] = "trabajando"

from dotenv import load_dotenv

# Límites: fuente única en plan_limits.py
from plan_limits import get_daily_limit, plan_sort_key
load_dotenv(os.path.join(_dir, ".env"))

# None = todos los usuarios activos; "jobs2" = solo ese usuario
SOLO_USUARIO = None



def _run() -> None:
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
    _saved_pw = getattr(_pa, '_global_pw', None)
    importlib.reload(_pa)
    if _saved_pw is not None:
        _pa._global_pw = _saved_pw
    from portal_accounts import get_trabajando_pw_session, apply_trabajando_playwright
    from scraper import _scrape_trabajando_playwright
    from trabajando_todos import _process_user

    all_users = bq.get_active_users()
    if SOLO_USUARIO:
        all_users = [u for u in all_users if (u.get("ID_USUARIO") or u.get("id")) == SOLO_USUARIO]

    print(f"[tbj_postulando] {len(all_users)} usuario(s) a procesar\n")

    total_ok = 0
    for user in all_users:
        uid    = user.get("ID_USUARIO") or user.get("id", "?")
        nombre = user.get("NOMBRE") or user.get("nombre") or uid
        plan   = (user.get("plan") or user.get("PLAN") or "FREE").upper()
        limite = get_daily_limit(user)

        # Cuántas postulaciones lleva hoy en TODOS los portales
        ya_hoy = bq.get_postulaciones_hoy(uid)
        cupo   = limite - ya_hoy

        print(f"\n{'='*60}")
        print(f"  USUARIO : {nombre} ({uid})  |  Plan: {plan}  |  Límite: {limite}")
        print(f"  Hoy     : {ya_hoy} postuladas  |  Cupo TBJ: {cupo}")
        print(f"{'='*60}")

        if cupo <= 0:
            print(f"  [skip] Cupo agotado ({ya_hoy}/{limite}) — sin postulaciones")
            continue

        try:
            res = _process_user(
                user, bq,
                get_trabajando_pw_session,
                apply_trabajando_playwright,
                _scrape_trabajando_playwright,
                max_count=cupo,
            )
            ok = res[0] if res else 0
            total_ok += ok
        except Exception as e:
            print(f"  [ERROR {uid}] {e}")

    print(f"\n{'='*60}")
    print(f"TOTAL TBJ: {total_ok} postulaciones / {len(all_users)} usuarios procesados")
    print(f"{'='*60}")


exc = [None]

def _thread() -> None:
    try:
        _run()
    except Exception as e:
        exc[0] = e

t = threading.Thread(target=_thread)
t.start()
t.join()
if exc[0]:
    raise exc[0]
