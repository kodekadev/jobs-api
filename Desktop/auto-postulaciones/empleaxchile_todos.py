"""
Postulaciones automáticas en EmpleaXChile para TODOS los usuarios activos.
Ejecutar desde Spyder: seleccionar todo y Run Selection, o F5.

Para un usuario específico, cambiar SOLO_USUARIO = "jobs2" (o None para todos).
"""
import os, sys, threading, asyncio

_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else r"C:\Users\bastian\Desktop\auto-postulaciones"
if _dir not in sys.path:
    sys.path.insert(0, _dir)

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")
os.environ["PORTALES_ACTIVOS"] = "empleaxchile"

from dotenv import load_dotenv
load_dotenv(os.path.join(_dir, ".env"))

SOLO_USUARIO =  "jobs267" 

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
    importlib.reload(_pa)
    import empleaxchile.postular as _exc_mod
    importlib.reload(_exc_mod)
    from empleaxchile.postular import postular_empleos_exc
    from empleaxchile.crear_cuenta import crear_cuenta_empleaxchile
    from telegram_notify import enviar as telegram

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    print(f"[config] ANTHROPIC_API_KEY: {'OK (' + str(len(api_key)) + ' chars)' if api_key else 'FALTA'}")

    all_users = bq.get_active_users()
    if SOLO_USUARIO:
        all_users = [u for u in all_users if (u.get("ID_USUARIO") or u.get("id")) == SOLO_USUARIO]

    print(f"[main] {len(all_users)} usuario(s) a procesar\n")

    total_ok = 0

    for user in all_users:
        uid    = user.get("ID_USUARIO") or user.get("id", "?")
        nombre = user.get("NOMBRE") or user.get("nombre") or uid
        plan   = user.get("PLAN") or user.get("plan") or "FREE"

        print(f"\n{'='*60}")
        print(f"  USUARIO: {nombre} ({uid}) | Plan: {plan}")
        print(f"{'='*60}")

        # Crear cuenta si no existe
        if not bq.get_portal_account(uid, "empleaxchile"):
            print(f"  [{uid}] Sin cuenta EmpleaXChile — creando...")
            try:
                ok = crear_cuenta_empleaxchile(uid, user)
                if not ok:
                    print(f"  [{uid}] No se pudo crear cuenta — saltar")
                    continue
            except Exception as e:
                print(f"  [{uid}] Error creando cuenta: {e}")
                continue

        # Límite diario
        max_dia  = _get_max_dia(plan)
        ya_hoy   = bq.get_postulaciones_hoy(uid)
        restantes = max(0, max_dia - ya_hoy)

        if restantes == 0:
            print(f"  [{uid}] Límite diario alcanzado ({ya_hoy}/{max_dia}) — saltar")
            continue

        print(f"  [{uid}] Postulaciones hoy: {ya_hoy}/{max_dia} | Restantes: {restantes}")

        ok_count = 0
        try:
            ok_count = postular_empleos_exc(uid, user, max_count=restantes)
        except Exception as e:
            print(f"  [ERROR {uid}] {e}")

        total_ok += ok_count
        print(f"\n  [{uid}] TOTAL: {ok_count} postulaciones EmpleaXChile")

        telegram(
            f"[AplicAI] EmpleaXChile: {ok_count} postulaciones\n"
            f"Usuario: {nombre} | Plan: {plan} | Límite: {max_dia}/día"
        )

    print(f"\n{'='*60}")
    print(f"Proceso finalizado — {total_ok} postulaciones | {len(all_users)} usuario(s)")


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
