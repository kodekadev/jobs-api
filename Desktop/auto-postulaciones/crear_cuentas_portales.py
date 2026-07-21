"""
Crea cuentas en Trabajando.cl y ChileTrabajos para todos los usuarios
que llenaron el formulario Postula Fácil, aunque no tengan auto-postulaciones activo.

Solo crea las cuentas y guarda las credenciales en BQ.
Para llenar el CV después, ejecutar completar_cvs_portales.py.

Ejecutar desde terminal o Spyder (F5):
    python crear_cuentas_portales.py              # todos los usuarios
    python crear_cuentas_portales.py jobs8        # solo un usuario
"""
import os, sys, threading, asyncio

_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else r"C:\Users\bastian\Desktop\auto-postulaciones"
if _dir not in sys.path:
    sys.path.insert(0, _dir)

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")

from dotenv import load_dotenv
load_dotenv(os.path.join(_dir, ".env"))

SOLO_USUARIO = None  # ej: "jobs8"


def _crear_cuentas_usuario(uid: str, user: dict, bq) -> None:
    nombre = user.get("NOMBRE") or user.get("nombre") or uid
    email  = user.get("EMAIL")  or user.get("email")  or ""

    print(f"\n{'='*60}")
    print(f"  USUARIO: {nombre} ({uid}) | {email}")
    print(f"{'='*60}")

    # ── Trabajando.cl ──────────────────────────────────────────────────────────
    cuenta_tbj = bq.get_portal_account(uid, "trabajando")
    if cuenta_tbj:
        print(f"  [{uid}] Trabajando: ya tiene cuenta ({cuenta_tbj['email']}) — saltar")
    else:
        print(f"  [{uid}] Trabajando: sin cuenta — creando...")
        try:
            from trabajando.crear_cuenta import crear_cuenta_trabajando
            ok = crear_cuenta_trabajando(uid, user)
            if ok:
                cuenta_tbj = bq.get_portal_account(uid, "trabajando")
                print(f"  [{uid}] Trabajando: OK ({cuenta_tbj['email'] if cuenta_tbj else '?'})")
            else:
                print(f"  [{uid}] Trabajando: no se pudo crear cuenta")
        except Exception as e:
            import traceback
            print(f"  [{uid}] Trabajando crear_cuenta ERROR: {e}")
            traceback.print_exc()

    # ── ChileTrabajos ──────────────────────────────────────────────────────────
    cuenta_cht = bq.get_portal_account(uid, "chiletrabajos")
    if cuenta_cht:
        print(f"  [{uid}] ChileTrabajos: ya tiene cuenta ({cuenta_cht.get('email', '?')}) — saltar")
    else:
        print(f"  [{uid}] ChileTrabajos: sin cuenta — creando...")
        try:
            from chiletrabajos.crear_cuenta import crear_cuenta_chiletrabajos
            ok = crear_cuenta_chiletrabajos(uid, user)
            if ok:
                cuenta_cht = bq.get_portal_account(uid, "chiletrabajos")
                print(f"  [{uid}] ChileTrabajos: OK ({cuenta_cht.get('email', '?') if cuenta_cht else '?'})")
            else:
                print(f"  [{uid}] ChileTrabajos: no se pudo crear cuenta")
        except Exception as e:
            import traceback
            print(f"  [{uid}] ChileTrabajos crear_cuenta ERROR: {e}")
            traceback.print_exc()

    print(f"  [{uid}] Listo")


def _run():
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.set_event_loop(asyncio.new_event_loop())

    import bq

    solo = SOLO_USUARIO or (sys.argv[1] if len(sys.argv) > 1 else None)

    all_users = bq.get_users_postula_facil()
    if solo:
        all_users = [u for u in all_users if (u.get("ID_USUARIO") or "").lower() == solo.lower()]

    print(f"[crear_cuentas] {len(all_users)} usuario(s) encontrado(s)")
    if not all_users:
        print("[crear_cuentas] Sin usuarios — verificar que existan registros en POSTULA_FACIL")
        return

    ok_count = 0
    for user in all_users:
        uid = user.get("ID_USUARIO") or "?"
        try:
            _crear_cuentas_usuario(uid, user, bq)
            ok_count += 1
        except Exception as e:
            import traceback
            print(f"  [ERROR {uid}] {e}")
            traceback.print_exc()

    print(f"\n{'='*60}")
    print(f"[crear_cuentas] Finalizado — {ok_count}/{len(all_users)} usuario(s) procesado(s)")


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
