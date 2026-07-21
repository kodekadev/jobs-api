"""
Setup de cuentas y CV para todos los usuarios activos.

Para cada usuario verifica si tiene cuenta en Trabajando.cl y ChileTrabajos.
Si no tiene, la crea, llena el CV y establece la sesión Playwright.

Ejecutar desde Spyder: F5 o Run File.
Ejecutar desde terminal:
    python setup_usuarios.py
    python setup_usuarios.py jobs8   ← solo un usuario
"""
import os, sys, threading, asyncio

_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else r"C:\Users\bastian\Desktop\auto-postulaciones"
if _dir not in sys.path:
    sys.path.insert(0, _dir)

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")

from dotenv import load_dotenv
load_dotenv(os.path.join(_dir, ".env"))

SOLO_USUARIO = None  # ej: "jobs8" para probar uno solo


def _setup_user(uid: str, user: dict, bq) -> None:
    nombre = user.get("NOMBRE") or uid

    print(f"\n{'='*60}")
    print(f"  SETUP: {nombre} ({uid})")
    print(f"{'='*60}")

    # ── Trabajando.cl ──────────────────────────────────────────────────────────
    cuenta_tbj = bq.get_portal_account(uid, "trabajando")
    if cuenta_tbj:
        print(f"  [{uid}] Trabajando: cuenta existente ({cuenta_tbj['email']})")
    else:
        print(f"  [{uid}] Trabajando: sin cuenta — creando...")
        try:
            from trabajando.crear_cuenta import crear_cuenta_trabajando
            ok = crear_cuenta_trabajando(uid, user)
            if ok:
                print(f"  [{uid}] Trabajando: cuenta creada OK")
                cuenta_tbj = bq.get_portal_account(uid, "trabajando")
            else:
                print(f"  [{uid}] Trabajando: no se pudo crear cuenta")
        except Exception as e:
            print(f"  [{uid}] Trabajando crear_cuenta error: {e}")

    if cuenta_tbj:
        try:
            from trabajando.completar_perfil import completar_perfil_trabajando
            print(f"  [{uid}] Trabajando: completando CV...")
            completar_perfil_trabajando(uid, user)
        except Exception as e:
            print(f"  [{uid}] Trabajando completar_perfil error: {e}")

    # ── ChileTrabajos ──────────────────────────────────────────────────────────
    cuenta_cht = bq.get_portal_account(uid, "chiletrabajos")
    if cuenta_cht:
        print(f"  [{uid}] ChileTrabajos: cuenta existente ({cuenta_cht.get('email', '?')})")
    else:
        print(f"  [{uid}] ChileTrabajos: sin cuenta — creando...")
        try:
            from chiletrabajos.crear_cuenta import crear_cuenta_chiletrabajos
            ok = crear_cuenta_chiletrabajos(uid, user)
            if ok:
                print(f"  [{uid}] ChileTrabajos: cuenta creada OK")
            else:
                print(f"  [{uid}] ChileTrabajos: no se pudo crear cuenta")
        except Exception as e:
            print(f"  [{uid}] ChileTrabajos crear_cuenta error: {e}")
        cuenta_cht = bq.get_portal_account(uid, "chiletrabajos")

    if cuenta_cht:
        try:
            from chiletrabajos.completar_perfil import _pw_completar_perfil_chiletrabajos
            print(f"  [{uid}] ChileTrabajos: completando CV...")
            _pw_completar_perfil_chiletrabajos(uid, user)
        except Exception as e:
            print(f"  [{uid}] ChileTrabajos completar_perfil error: {e}")

    print(f"  [{uid}] Setup completado")


def _run():
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.set_event_loop(asyncio.new_event_loop())

    import bq

    solo = SOLO_USUARIO or (sys.argv[1] if len(sys.argv) > 1 else None)

    all_users = bq.get_active_users()
    if solo:
        all_users = [u for u in all_users if (u.get("ID_USUARIO") or u.get("id")) == solo]

    print(f"[setup] {len(all_users)} usuario(s) a configurar")

    for user in all_users:
        uid = user.get("ID_USUARIO") or user.get("id", "?")
        try:
            _setup_user(uid, user, bq)
        except Exception as e:
            print(f"  [ERROR {uid}] {e}")

    print(f"\n[setup] Proceso finalizado — {len(all_users)} usuario(s)")


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
