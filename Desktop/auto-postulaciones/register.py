"""
Registro automático de usuarios en portales de empleo.

Corre como Cloud Run Job con MODE=register.
Toma SINGLE_USER_ID del entorno y crea cuentas en los portales configurados.
"""

import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

import time
import bq
from portal_accounts import get_or_create_account, upload_cv_trabajando, crear_cv_interno_trabajando
from telegram_notify import enviar as telegram

# Indeed queda fuera del auto-registro: requiere OTP interactivo del usuario
PORTALES = ["chiletrabajos", "trabajando"]

PORTAL_LABELS = {"chiletrabajos": "ChileTrabajos.cl", "trabajando": "Trabajando.cl"}


def register_user(user: dict) -> dict:
    uid    = user.get("ID_USUARIO") or user.get("id_usuario", "")
    nombre = user.get("NOMBRE") or user.get("nombre") or uid
    resultados = {}

    # Filtrar portales donde ya existe cuenta (evita ejecuciones paralelas duplicadas)
    portales_pendientes = [p for p in PORTALES if not bq.get_portal_account(uid, p)]
    if not portales_pendientes:
        print(f"\n[{nombre}] Cuenta ya existe en todos los portales — nada que hacer")
        return {p: "YA_EXISTE" for p in PORTALES}

    print(f"\n[{nombre}] Registrando en portales: {portales_pendientes}")
    t_inicio = time.time()

    cv_url = user.get("cv_url") or user.get("CV_URL") or ""

    for portal in portales_pendientes:
        label = PORTAL_LABELS.get(portal, portal)
        # ── 1. Crear cuenta ───────────────────────────────────────────────────
        cuenta_preexistia = bq.get_portal_account(uid, portal) is not None
        try:
            cuenta = get_or_create_account(user, portal)
        except Exception as e:
            resultados[portal] = "ERROR"
            print(f"  [{portal}] excepción al crear cuenta: {e}")
            telegram(
                f"[PostulAI] Onboarding ERROR\n"
                f"Usuario: {nombre}\n"
                f"Portal: {label}\n"
                f"No se pudo crear cuenta: {e}"
            )
            continue
        cuenta_nueva = not cuenta_preexistia and cuenta is not None
        if not cuenta:
            resultados[portal] = "ERROR"
            print(f"  [{portal}] no se pudo crear cuenta")
            telegram(
                f"[PostulAI] Onboarding ERROR\n"
                f"Usuario: {nombre}\n"
                f"Portal: {label}\n"
                f"No se pudo crear cuenta (sin excepción)"
            )
            continue

        resultados[portal] = "OK"
        print(f"  [{portal}] cuenta lista — {cuenta['email']}")
        telegram(
            f"[PostulAI] Cuenta creada\n"
            f"Usuario: {nombre}\n"
            f"Portal: {label}\n"
            f"Email: {cuenta['email']}"
        )

        # ── 1b. Onboarding ChileTrabajos: completar perfil + CV ───────────────
        if portal == "chiletrabajos":
            perfil_ok = False
            if cuenta_nueva:
                # El perfil fue completado en la misma sesion de creacion de cuenta
                perfil_ok = True
                print(f"  [{portal}] Perfil completado durante creacion de cuenta")
            else:
                try:
                    from chiletrabajos.completar_perfil import completar_perfil_chiletrabajos
                    print(f"  [{portal}] Completando perfil...")
                    perfil_ok = completar_perfil_chiletrabajos(uid, user)
                except Exception as e:
                    print(f"  [{portal}] Error completando perfil: {e}")
            telegram(
                f"[PostulAI] Onboarding completado\n"
                f"{'─' * 22}\n"
                f"Usuario: {nombre}\n"
                f"Portal: {label}\n"
                f"Email: {cuenta['email']}\n"
                f"Cuenta: OK\n"
                f"Perfil: {'OK' if perfil_ok else 'ERROR'}\n"
                f"Listo para postular!"
            )
            continue

        # ── 2. Subir CV / wizard ─────────────────────────────────────────────
        cv_ok = False
        cv_interno_ok = False
        if portal == "trabajando":
            if cuenta_nueva:
                # Wizard ya completado por crear_cuenta_trabajando en la misma sesion
                cv_ok = True
                cv_interno_ok = True
                print(f"  [{portal}] Wizard CV completado durante creacion de cuenta")
            elif cv_url:
                print(f"  [{portal}] Subiendo CV archivo (cuenta pre-existente)...")
                cv_ok = upload_cv_trabajando(cuenta["email"], cuenta["password"], cv_url, user=user)
                cv_interno_ok = cv_ok
                telegram(
                    f"[PostulAI] CV subido\n"
                    f"Usuario: {nombre}\n"
                    f"Archivo: {'OK' if cv_ok else 'ERROR'}\n"
                    f"Formato Trabajando.com: {'OK' if cv_interno_ok else 'ERROR'}"
                )
            else:
                print(f"  [{portal}] Sin CV archivo — completando CV interno...")
                cv_interno_ok = crear_cv_interno_trabajando(cuenta["email"], cuenta["password"], user=user)
                telegram(
                    f"[PostulAI] CV formato Trabajando.com\n"
                    f"Usuario: {nombre}\n"
                    f"Estado: {'OK' if cv_interno_ok else 'ERROR (sin PDF)'}"
                )

        duracion = (time.time() - t_inicio) / 60
        # ── 3. Resumen final de onboarding ────────────────────────────────────
        telegram(
            f"[PostulAI] Onboarding completado\n"
            f"{'─' * 22}\n"
            f"Usuario: {nombre}\n"
            f"Portal: Trabajando.cl\n"
            f"Email: {cuenta['email']}\n"
            f"Cuenta: OK\n"
            f"CV archivo: {'OK' if cv_ok else 'sin PDF'}\n"
            f"CV Trabajando.com: {'OK' if cv_interno_ok else 'pendiente'}\n"
            f"Tiempo: {duracion:.1f} min\n"
            f"Listo para postular!"
        )

    return resultados


def main():
    single_user_id = os.environ.get("SINGLE_USER_ID", "").strip()

    if not single_user_id:
        print("ERROR: SINGLE_USER_ID no definido")
        sys.exit(1)

    print(f"Registrando usuario: {single_user_id}")

    users = bq.get_user_by_id(single_user_id)
    if not users:
        print(f"ERROR: usuario {single_user_id} no encontrado en BigQuery")
        sys.exit(1)

    user = users[0]
    resultados = register_user(user)

    errores = [p for p, v in resultados.items() if v == "ERROR"]
    if errores:
        telegram(
            f"[PostulAI] Registro FALLIDO\n"
            f"Usuario: {user.get('NOMBRE') or single_user_id}\n"
            f"Portales con error: {', '.join(PORTAL_LABELS.get(p, p) for p in errores)}"
        )

    ok = all(v in ("OK", "YA_EXISTE") for v in resultados.values())
    print(f"\nResultado: {resultados}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
