"""
ChileTrabajos — postulaciones respetando cupo diario global del usuario.

Consulta cuántas postulaciones lleva el usuario HOY (en todos los portales),
calcula el cupo restante y solo postula hasta ese límite.
Si otro portal ya agotó el cupo, este script no hace nada para ese usuario.

Uso desde Spyder: seleccionar todo y Run Selection, o F5.
Para un usuario específico: SOLO_USUARIO = "jobs2"
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else r"C:\Users\bastian\Desktop\auto-postulaciones"

# None = todos los usuarios activos; "jobs2" = solo ese usuario
SOLO_USUARIO = None
SOLO_USUARIO = "jobs2"

N_WORKERS = 1  # procesos en paralelo

PLAN_ORDER  = {"PREMIUM": 0, "TURBO": 1, "PRO": 2, "TRIAL": 3, "FREE": 4}
PLAN_LIMITS = {"FREE": 5, "TRIAL": 10, "PRO": 25, "TURBO": 40, "PREMIUM": 50}


def _procesar_usuario(uid: str) -> int:
    """Corre en un proceso hijo independiente — cada uno tiene su propio Playwright."""
    try:
        import sys, os, asyncio
        from dotenv import load_dotenv

        _d = r"C:\Users\bastian\Desktop\auto-postulaciones"
        if _d not in sys.path:
            sys.path.insert(0, _d)

        os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")
        os.environ["PORTALES_ACTIVOS"] = "chiletrabajos"
        load_dotenv(os.path.join(_d, ".env"))

        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        asyncio.set_event_loop(asyncio.new_event_loop())

        import bq
        from chiletrabajos.postular import postular_empleos_cht

        all_users = bq.get_active_users()
        user = next((u for u in all_users if (u.get("ID_USUARIO") or u.get("id")) == uid), None)
        if not user:
            print(f"[{uid}] No encontrado en usuarios activos")
            return 0

        nombre = user.get("NOMBRE") or user.get("nombre") or uid
        plan   = (user.get("PLAN") or user.get("plan") or "FREE").upper()
        limite = PLAN_LIMITS.get(plan, PLAN_LIMITS["FREE"])

        creds = bq.get_portal_account(uid, "chiletrabajos")
        if not creds:
            print(f"[{uid}] Sin cuenta ChileTrabajos — saltando")
            return 0

        ya_hoy = bq.get_postulaciones_hoy(uid)
        cupo   = limite - ya_hoy

        print(f"\n{'='*60}")
        print(f"  USUARIO : {nombre} ({uid})  |  Plan: {plan}  |  Límite: {limite}")
        print(f"  Hoy     : {ya_hoy} postuladas  |  Cupo CHT: {cupo}")
        print(f"{'='*60}")

        if cupo <= 0:
            print(f"  [skip] Cupo agotado ({ya_hoy}/{limite}) — sin postulaciones")
            return 0

        return postular_empleos_cht(uid, user, max_count=cupo) or 0

    except Exception as e:
        import traceback
        print(f"  [ERROR {uid}] {e}")
        traceback.print_exc()
        return 0


def _run() -> None:
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

    os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")
    os.environ["PORTALES_ACTIVOS"] = "chiletrabajos"

    from dotenv import load_dotenv
    load_dotenv(os.path.join(_dir, ".env"))

    import bq

    all_users = bq.get_active_users()
    if SOLO_USUARIO:
        all_users = [u for u in all_users if (u.get("ID_USUARIO") or u.get("id")) == SOLO_USUARIO]

    # Priorizar por plan: PREMIUM primero, FREE último
    all_users.sort(key=lambda u: PLAN_ORDER.get((u.get("PLAN") or u.get("plan") or "FREE").upper(), 4))

    uids = [u.get("ID_USUARIO") or u.get("id") for u in all_users]
    print(f"[cht_postulando] {len(uids)} usuario(s) a procesar | {N_WORKERS} procesos\n")

    total_ok = 0
    # Cuando hay 1 usuario (modo interactivo/debug), correr directo en el proceso
    # principal para que el output aparezca en Spyder/IPython sin delay.
    if len(uids) == 1:
        total_ok = _procesar_usuario(uids[0])
    else:
        with ProcessPoolExecutor(max_workers=N_WORKERS) as pool:
            futures = {pool.submit(_procesar_usuario, uid): uid for uid in uids}
            for fut in as_completed(futures):
                uid = futures[fut]
                try:
                    total_ok += fut.result()
                except Exception as e:
                    print(f"  [ERROR {uid}] {e}")

    print(f"\n{'='*60}")
    print(f"TOTAL CHT: {total_ok} postulaciones / {len(uids)} usuarios procesados")
    print(f"{'='*60}")


if __name__ == "__main__":
    _run()
