"""
Crea cuentas en Laborum para todos los usuarios de Postula Fácil.

Ejecutar:
    python crear_cuentas_laborum.py           # todos los usuarios
    python crear_cuentas_laborum.py jobs8     # solo un usuario
"""
import os, sys, threading, asyncio

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

SOLO_USUARIO = None  # ej: "jobs8"


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

    import bq
    from laborum.crear_cuenta import crear_cuenta_laborum

    solo = SOLO_USUARIO or (sys.argv[1] if len(sys.argv) > 1 else None)
    all_users = bq.get_users_postula_facil()
    if solo:
        all_users = [u for u in all_users if (u.get("ID_USUARIO") or "").lower() == solo.lower()]

    print(f"[laborum] {len(all_users)} usuario(s)")
    ok, skip, fail = 0, 0, 0
    for user in all_users:
        uid = user.get("ID_USUARIO") or "?"
        nombre = user.get("NOMBRE") or uid
        print(f"\n{'='*55}\n  {nombre} ({uid})\n{'='*55}")
        try:
            if bq.get_portal_account(uid, "laborum"):
                print(f"  [{uid}] ya tiene cuenta — saltar")
                skip += 1
                continue
            if crear_cuenta_laborum(uid, user):
                ok += 1
            else:
                fail += 1
        except Exception as e:
            import traceback
            print(f"  [ERROR {uid}] {e}")
            traceback.print_exc()
            fail += 1

    print(f"\n[laborum] OK={ok} skip={skip} fail={fail} / {len(all_users)}")


if __name__ == "__main__":
    _in_jenkins = bool(os.environ.get("BUILD_NUMBER") or os.environ.get("JENKINS_URL"))
    _in_interactive = (
        "spyder" in sys.modules or "IPython" in sys.modules or
        bool(os.environ.get("SPYDER_ARGS")) or bool(os.environ.get("JPY_SESSION_NAME"))
    )
    _use_hard_exit = _in_jenkins and not _in_interactive

    exc = [None]
    def _thread():
        try: _run()
        except Exception as e: exc[0] = e

    t = threading.Thread(target=_thread)
    t.start(); t.join()
    if exc[0]:
        print(f"[FATAL] {exc[0]}")
        import traceback; traceback.print_exception(type(exc[0]), exc[0], exc[0].__traceback__)
        if _use_hard_exit: os._exit(1)
    elif _use_hard_exit:
        os._exit(0)
