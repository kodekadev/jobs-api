"""
Laborum — postulaciones respetando cupo diario global del usuario.

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
import json

_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else r"C:\Users\bastian\Desktop\auto-postulaciones"
if _dir not in sys.path:
    sys.path.insert(0, _dir)

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")
os.environ["PORTALES_ACTIVOS"] = "laborum"

from dotenv import load_dotenv

# Límites: fuente única en plan_limits.py
from plan_limits import get_daily_limit, plan_sort_key
load_dotenv(os.path.join(_dir, ".env"))

# None = todos los usuarios activos; "jobs2" = solo ese usuario
SOLO_USUARIO = None



def _parse_json_field(val) -> list:
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return [val] if val else []
    return []


def _run() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.set_event_loop(asyncio.new_event_loop())

    import importlib
    import bq
    import laborum.postular as _lab_mod
    importlib.reload(_lab_mod)
    from laborum.postular import buscar_y_postular_lab

    all_users = bq.get_active_users()
    if SOLO_USUARIO:
        all_users = [u for u in all_users if (u.get("ID_USUARIO") or u.get("id")) == SOLO_USUARIO]

    # Priorizar por plan: PREMIUM primero, FREE último
    all_users.sort(key=plan_sort_key)

    print(f"[lab_postulando] {len(all_users)} usuario(s) a procesar\n")

    total_ok = 0
    for user in all_users:
        uid    = user.get("ID_USUARIO") or user.get("id", "?")
        nombre = user.get("NOMBRE") or user.get("nombre") or uid
        plan   = (user.get("plan") or user.get("PLAN") or "FREE").upper()
        limite = get_daily_limit(user)

        creds = bq.get_portal_account(uid, "laborum")
        if not creds:
            print(f"[{uid}] Sin cuenta Laborum — saltando")
            continue

        cargos     = _parse_json_field(user.get("CARGOS") or user.get("cargos") or [])
        ubicaciones = _parse_json_field(user.get("UBICACIONES") or user.get("ubicaciones") or ["Santiago"])
        ubicacion  = (ubicaciones[0] if ubicaciones else "Santiago").split(",")[0].strip()

        if not cargos:
            print(f"[{uid}] Sin cargos configurados — saltando")
            continue

        # Cuántas postulaciones lleva hoy en TODOS los portales
        ya_hoy = bq.get_postulaciones_hoy(uid)
        cupo   = limite - ya_hoy

        print(f"\n{'='*60}")
        print(f"  USUARIO : {nombre} ({uid})  |  Plan: {plan}  |  Límite: {limite}")
        print(f"  Hoy     : {ya_hoy} postuladas  |  Cupo LAB: {cupo}")
        print(f"{'='*60}")

        if cupo <= 0:
            print(f"  [skip] Cupo agotado ({ya_hoy}/{limite}) — sin postulaciones")
            continue

        try:
            ok = buscar_y_postular_lab(uid, user, cargos, ubicacion, max_n=cupo)
            total_ok += ok or 0
        except Exception as e:
            print(f"  [ERROR {uid}] {e}")

    print(f"\n{'='*60}")
    print(f"TOTAL LAB: {total_ok} postulaciones / {len(all_users)} usuarios procesados")
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
