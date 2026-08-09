"""
Crea cuentas en los portales para todos los usuarios que llenaron Postula Fácil.

Ejecutar desde terminal o Spyder (F5):
    python crear_cuentas_portales.py                          # todos los portales, todos los usuarios
    python crear_cuentas_portales.py trabajando               # solo Trabajando.cl
    python crear_cuentas_portales.py chiletrabajos            # solo ChileTrabajos
    python crear_cuentas_portales.py computrabajo             # solo Computrabajo
    python crear_cuentas_portales.py laborum                  # solo Laborum
    python crear_cuentas_portales.py trabajando jobs8         # Trabajando.cl, usuario jobs8

Para correr los 4 portales en paralelo (un proceso por portal):
    start python crear_cuentas_portales.py trabajando
    start python crear_cuentas_portales.py chiletrabajos
    start python crear_cuentas_portales.py computrabajo
    start python crear_cuentas_portales.py laborum
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

PORTALES_VALIDOS = ["trabajando", "chiletrabajos", "computrabajo", "laborum"]

# Sobrescribir desde código si se quiere fijar portal/usuario sin usar CLI
SOLO_PORTAL  = None  # ej: "trabajando"
SOLO_USUARIO = None  # ej: "jobs8"


def _crear_cuenta_portal(uid: str, user: dict, portal: str, bq) -> None:
    cuenta = bq.get_portal_account(uid, portal)
    if cuenta:
        print(f"  [{uid}] {portal}: ya tiene cuenta ({cuenta.get('email', '?')}) — saltar")
        return

    print(f"  [{uid}] {portal}: sin cuenta — creando...")
    try:
        if portal == "trabajando":
            from trabajando.crear_cuenta import crear_cuenta_trabajando
            ok = crear_cuenta_trabajando(uid, user)
        elif portal == "chiletrabajos":
            from chiletrabajos.crear_cuenta import crear_cuenta_chiletrabajos
            ok = crear_cuenta_chiletrabajos(uid, user)
        elif portal == "computrabajo":
            from computrabajo.crear_cuenta import crear_cuenta_computrabajo
            ok = crear_cuenta_computrabajo(uid, user)
        elif portal == "laborum":
            from laborum.crear_cuenta import crear_cuenta_laborum
            ok = crear_cuenta_laborum(uid, user)
        else:
            print(f"  [{uid}] {portal}: portal no reconocido")
            return

        if ok:
            cuenta = bq.get_portal_account(uid, portal)
            print(f"  [{uid}] {portal}: OK ({cuenta.get('email', '?') if cuenta else '?'})")
        else:
            print(f"  [{uid}] {portal}: no se pudo crear cuenta")
    except Exception as e:
        import traceback
        print(f"  [{uid}] {portal} ERROR: {e}")
        traceback.print_exc()


def _run(portales: list[str], solo_usuario: str | None):
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

    all_users = bq.get_users_postula_facil()
    if solo_usuario:
        all_users = [u for u in all_users if (u.get("ID_USUARIO") or "").lower() == solo_usuario.lower()]

    portales_str = ", ".join(portales)
    print(f"[crear_cuentas] portales={portales_str} | {len(all_users)} usuario(s)")
    if not all_users:
        print("[crear_cuentas] Sin usuarios — verificar registros en POSTULA_FACIL")
        return

    ok_count = 0
    for user in all_users:
        uid = user.get("ID_USUARIO") or "?"
        nombre = user.get("NOMBRE") or uid
        print(f"\n{'='*60}")
        print(f"  USUARIO: {nombre} ({uid})")
        print(f"{'='*60}")
        try:
            for portal in portales:
                _crear_cuenta_portal(uid, user, portal, bq)
            ok_count += 1
        except Exception as e:
            import traceback
            print(f"  [ERROR {uid}] {e}")
            traceback.print_exc()

    print(f"\n{'='*60}")
    print(f"[crear_cuentas] Finalizado — {ok_count}/{len(all_users)} usuario(s) | portales: {portales_str}")


if __name__ == "__main__":
    # Parsear argumentos: [portal] [uid]
    args = [a for a in sys.argv[1:] if not a.startswith("-")]

    portal_arg  = None
    usuario_arg = None
    for a in args:
        if a.lower() in PORTALES_VALIDOS:
            portal_arg = a.lower()
        else:
            usuario_arg = a  # asume que es un uid

    portales_run  = [SOLO_PORTAL or portal_arg] if (SOLO_PORTAL or portal_arg) else PORTALES_VALIDOS
    usuario_run   = SOLO_USUARIO or usuario_arg

    _in_jenkins = bool(os.environ.get("BUILD_NUMBER") or os.environ.get("JENKINS_URL"))
    _in_interactive = (
        "spyder" in sys.modules or
        "IPython" in sys.modules or
        bool(os.environ.get("SPYDER_ARGS")) or
        bool(os.environ.get("JPY_SESSION_NAME"))
    )
    _use_hard_exit = _in_jenkins and not _in_interactive

    exc = [None]

    def _thread():
        try:
            _run(portales_run, usuario_run)
        except Exception as e:
            exc[0] = e

    t = threading.Thread(target=_thread)
    t.start()
    t.join()
    if exc[0]:
        print(f"[FATAL] {exc[0]}")
        import traceback; traceback.print_exception(type(exc[0]), exc[0], exc[0].__traceback__)
        if _use_hard_exit:
            os._exit(1)
    elif _use_hard_exit:
        os._exit(0)
