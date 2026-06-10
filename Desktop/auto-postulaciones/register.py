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

    print(f"\n[{nombre}] Registrando en portales: {PORTALES}")
    t_inicio = time.time()

    cv_url = user.get("cv_url") or user.get("CV_URL") or ""

    for portal in PORTALES:
        label = PORTAL_LABELS.get(portal, portal)
        # ── 1. Crear cuenta ───────────────────────────────────────────────────
        cuenta = get_or_create_account(user, portal)
        if not cuenta:
            resultados[portal] = "ERROR"
            print(f"  [{portal}] no se pudo crear cuenta")
            telegram(
                f"[PostulAI] Onboarding\n"
                f"Usuario: {nombre}\n"
                f"Portal: {label}\n"
                f"ERROR: no se pudo crear cuenta"
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

        # ── 2. Subir CV archivo ───────────────────────────────────────────────
        cv_ok = False
        cv_interno_ok = False
        if portal == "trabajando":
            if cv_url:
                print(f"  [{portal}] Subiendo CV archivo...")
                cv_ok = upload_cv_trabajando(cuenta["email"], cuenta["password"], cv_url, user=user)
                cv_interno_ok = cv_ok
                telegram(
                    f"[PostulAI] CV subido\n"
                    f"Usuario: {nombre}\n"
                    f"Archivo: {'OK' if cv_ok else 'ERROR'}\n"
                    f"Formato Trabajando.com: {'OK' if cv_interno_ok else 'ERROR'}"
                )
            else:
                # Sin PDF, completar wizard con datos del perfil
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

    ok = all(v == "OK" for v in resultados.values())
    print(f"\nResultado: {resultados}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
