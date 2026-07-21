"""
Completa el CV en Trabajando.cl y ChileTrabajos para todos los usuarios
que ya tienen cuenta creada en esos portales.

Requiere que las cuentas ya existan (ejecutar crear_cuentas_portales.py primero).
Se puede re-ejecutar sin problema si el CV ya fue llenado — los portales
actualizarán los datos con los valores actuales de Postula Fácil.

Ejecutar desde terminal o Spyder (F5):
    python completar_cvs_portales.py              # todos los usuarios
    python completar_cvs_portales.py jobs8        # solo un usuario
"""
import os, sys, threading, asyncio

_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else r"C:\Users\bastian\Desktop\auto-postulaciones"
if _dir not in sys.path:
    sys.path.insert(0, _dir)

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")

from dotenv import load_dotenv
load_dotenv(os.path.join(_dir, ".env"))

SOLO_USUARIO = None  # ej: "jobs8"


def _completar_cv_usuario(uid: str, user: dict, bq) -> None:
    nombre = user.get("NOMBRE") or user.get("nombre") or uid
    email  = user.get("EMAIL")  or user.get("email")  or ""

    print(f"\n{'='*60}")
    print(f"  USUARIO: {nombre} ({uid}) | {email}")
    print(f"{'='*60}")

    hash_actual = bq.perfil_hash(user)

    # ── Trabajando.cl ──────────────────────────────────────────────────────────
    cuenta_tbj = bq.get_portal_account(uid, "trabajando")
    if not cuenta_tbj:
        print(f"  [{uid}] Trabajando: sin cuenta — ejecutar crear_cuentas_portales.py primero")
    elif not bq.perfil_necesita_actualizar(uid, "trabajando", hash_actual):
        print(f"  [{uid}] Trabajando: perfil ya actualizado y sin cambios — saltar")
    else:
        print(f"  [{uid}] Trabajando: completando CV ({cuenta_tbj['email']})...")
        try:
            from trabajando.completar_perfil import completar_perfil_trabajando
            ok = completar_perfil_trabajando(uid, user)
            if ok:
                bq.save_perfil_completado(uid, "trabajando", hash_actual)
                print(f"  [{uid}] Trabajando: CV completado OK — hash guardado")
            else:
                print(f"  [{uid}] Trabajando: completar_perfil retornó False")
        except Exception as e:
            import traceback
            print(f"  [{uid}] Trabajando completar_perfil ERROR: {e}")
            traceback.print_exc()

    # ── ChileTrabajos ──────────────────────────────────────────────────────────
    cuenta_cht = bq.get_portal_account(uid, "chiletrabajos")
    if not cuenta_cht:
        print(f"  [{uid}] ChileTrabajos: sin cuenta — ejecutar crear_cuentas_portales.py primero")
    elif not bq.perfil_necesita_actualizar(uid, "chiletrabajos", hash_actual):
        print(f"  [{uid}] ChileTrabajos: perfil ya actualizado y sin cambios — saltar")
    else:
        print(f"  [{uid}] ChileTrabajos: completando CV ({cuenta_cht.get('email', '?')})...")
        try:
            from chiletrabajos.completar_perfil import _pw_completar_perfil_chiletrabajos
            ok = _pw_completar_perfil_chiletrabajos(uid, user)
            if ok:
                bq.save_perfil_completado(uid, "chiletrabajos", hash_actual)
                print(f"  [{uid}] ChileTrabajos: CV completado OK — hash guardado")
            else:
                print(f"  [{uid}] ChileTrabajos: completar_perfil retornó False")
        except Exception as e:
            import traceback
            print(f"  [{uid}] ChileTrabajos completar_perfil ERROR: {e}")
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

    print(f"[completar_cvs] {len(all_users)} usuario(s) encontrado(s)")
    if not all_users:
        print("[completar_cvs] Sin usuarios — verificar que existan registros en POSTULA_FACIL")
        return

    ok_count = 0
    for user in all_users:
        uid = user.get("ID_USUARIO") or "?"
        try:
            _completar_cv_usuario(uid, user, bq)
            ok_count += 1
        except Exception as e:
            import traceback
            print(f"  [ERROR {uid}] {e}")
            traceback.print_exc()

    print(f"\n{'='*60}")
    print(f"[completar_cvs] Finalizado — {ok_count}/{len(all_users)} usuario(s) procesado(s)")


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
