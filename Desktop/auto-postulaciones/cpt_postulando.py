"""
Computrabajo — postulaciones respetando cupo diario global del usuario.

Consulta cuántas postulaciones lleva el usuario HOY (en todos los portales),
calcula el cupo restante y solo postula hasta ese límite.
Si otro portal ya agotó el cupo, este script no hace nada para ese usuario.

Uso desde Spyder: seleccionar todo y Run Selection, o F5.
Para un usuario específico: SOLO_USUARIO = "jobs2"
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

# Límites: fuente única en plan_limits.py
from plan_limits import get_daily_limit, plan_sort_key

_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else r"C:\Users\bastian\Desktop\auto-postulaciones"

# None = todos los usuarios activos; "jobs2" = solo ese usuario
SOLO_USUARIO = None
# SOLO_USUARIO = "jobs2"

N_WORKERS = 1  # procesos en paralelo



def _procesar_usuario(uid: str) -> tuple[int, str]:
    """Corre en un proceso hijo independiente — cada uno tiene su propio Playwright.
    Retorna (postulaciones_ok, motivo_skip)."""
    try:
        import sys, os, asyncio
        from dotenv import load_dotenv

        os.environ["PYTHONIOENCODING"] = "utf-8"
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass

        _d = r"C:\Users\bastian\Desktop\auto-postulaciones"
        if _d not in sys.path:
            sys.path.insert(0, _d)

        os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")
        os.environ["PORTALES_ACTIVOS"] = "computrabajo"
        load_dotenv(os.path.join(_d, ".env"))

        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        asyncio.set_event_loop(asyncio.new_event_loop())

        import bq
        from computrabajo.postular import postular_empleos_cpt

        all_users = bq.get_active_users()
        user = next((u for u in all_users if (u.get("ID_USUARIO") or u.get("id")) == uid), None)
        if not user:
            return 0, "no_encontrado"

        nombre = user.get("NOMBRE") or user.get("nombre") or uid
        plan   = (user.get("PLAN") or user.get("plan") or "FREE").upper()
        limite = get_daily_limit(user)

        creds = bq.get_portal_account(uid, "computrabajo")
        if not creds:
            return 0, "sin_cuenta_cpt"

        ya_hoy = bq.get_postulaciones_hoy(uid)
        cupo   = limite - ya_hoy

        print(f"\n{'='*60}")
        print(f"  USUARIO : {nombre} ({uid})  |  Plan: {plan}  |  Límite: {limite}")
        print(f"  Hoy     : {ya_hoy} postuladas  |  Cupo CPT: {cupo}")
        print(f"{'='*60}")

        if cupo <= 0:
            print(f"  [skip] Cupo agotado ({ya_hoy}/{limite}) — sin postulaciones")
            return 0, f"cupo_agotado({ya_hoy}/{limite})"

        result = postular_empleos_cpt(uid, user, max_n=cupo) or 0
        return result, "" if result > 0 else "sin_postulaciones"

    except Exception as e:
        import traceback
        print(f"  [ERROR {uid}] {e}")
        traceback.print_exc()
        return 0, f"error: {e}"


def _run() -> None:
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")
    os.environ["PORTALES_ACTIVOS"] = "computrabajo"

    from dotenv import load_dotenv
    load_dotenv(os.path.join(_dir, ".env"))

    import bq

    all_users = bq.get_active_users()
    if SOLO_USUARIO:
        all_users = [u for u in all_users if (u.get("ID_USUARIO") or u.get("id")) == SOLO_USUARIO]

    # Priorizar por plan: PREMIUM primero, FREE último
    all_users.sort(key=plan_sort_key)

    uids = [u.get("ID_USUARIO") or u.get("id") for u in all_users]
    print(f"[cpt_postulando] {len(uids)} usuario(s) a procesar | {N_WORKERS} procesos\n")

    total_ok = 0
    from collections import Counter
    skip_counts: Counter = Counter()

    with ProcessPoolExecutor(max_workers=N_WORKERS) as pool:
        futures = {pool.submit(_procesar_usuario, uid): uid for uid in uids}
        for fut in as_completed(futures):
            uid = futures[fut]
            try:
                count, motivo = fut.result()
                total_ok += count
                if count == 0:
                    skip_counts[motivo] += 1
            except Exception as e:
                print(f"  [ERROR {uid}] {e}")
                skip_counts[f"excepcion"] += 1

    print(f"\n{'='*60}")
    print(f"TOTAL CPT: {total_ok} postulaciones / {len(uids)} usuarios procesados")
    print(f"\nDesglose de usuarios sin postulaciones:")
    for motivo, n in skip_counts.most_common():
        print(f"  {n:4d}  {motivo}")
    print(f"{'='*60}")


if __name__ == "__main__":
    _run()
