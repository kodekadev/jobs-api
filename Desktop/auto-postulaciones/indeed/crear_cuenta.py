"""
Indeed — Creación de cuenta de candidato.

Diferencia clave vs Trabajando/ChileTrabajos:
  Indeed exige verificación de email (código OTP) al registrarse.
  Por eso usamos el EMAIL real del usuario (no un fake generado),
  y pausamos el flujo para que ingrese el código manualmente.

Flujo:
  1. Genera contraseña segura (guardada en BigQuery)
  2. Navega a cl.indeed.com/account/register
  3. Llena email → contraseña → nombre
  4. Pausa y espera que el operador ingrese el código OTP recibido en su email
  5. Guarda credenciales + cookies en BigQuery CUENTAS_PORTALES

Uso:
    python indeed/crear_cuenta.py --user jobs2
    python indeed/crear_cuenta.py --user jobs2 --email otro@mail.com
"""
import os
import sys
import re
import time
import string
import secrets
import random

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

sys.stdout.reconfigure(encoding="utf-8")

_in_cloud_run = bool(os.environ.get("K_SERVICE"))
HEADLESS = os.environ.get("PLAYWRIGHT_HEADLESS", "1" if _in_cloud_run else "0") != "0"

PORTAL_ID    = "indeed"
REGISTER_URL = "https://cl.indeed.com/account/register"


# ── helpers ───────────────────────────────────────────────────────────────────

def _generar_clave() -> str:
    chars = string.ascii_letters + string.digits + "!@#$"
    while True:
        pwd = "".join(secrets.choice(chars) for _ in range(14))
        if (any(c.isupper() for c in pwd)
                and any(c.islower() for c in pwd)
                and any(c.isdigit() for c in pwd)
                and any(c in "!@#$" for c in pwd)):
            return pwd


def _human_type(page, selector: str, text: str) -> None:
    el = page.locator(selector).first
    el.click()
    time.sleep(random.uniform(0.3, 0.6))
    for ch in text:
        page.keyboard.type(ch, delay=random.randint(70, 180))
    time.sleep(random.uniform(0.2, 0.4))


def _click_first(page, selectors: list[str], timeout: int = 4000) -> bool:
    for sel in selectors:
        try:
            page.locator(sel).first.wait_for(timeout=timeout)
            page.locator(sel).first.click()
            return True
        except Exception:
            continue
    return False


_OTP_SELECTORS = [
    "input[name='__verificationCode']",
    "input[autocomplete='one-time-code']",
    "input[placeholder*='digo']",
    "input[placeholder*='verification']",
    "input[placeholder*='code']",
]
_OTP_TEXTS = [
    "digo de verificaci", "verification code", "verifica", "confirma tu email",
    "check your email", "revisa tu correo", "sent you an email",
]


def _hay_otp_en_pantalla(page) -> bool:
    content = page.content().lower()
    if any(t in content for t in _OTP_TEXTS):
        return True
    return any(page.locator(s).count() > 0 for s in _OTP_SELECTORS)


def _ingresar_otp_en_pagina(page, code: str) -> bool:
    for sel in _OTP_SELECTORS:
        try:
            page.locator(sel).first.wait_for(timeout=2000)
            page.locator(sel).first.fill(code)
            time.sleep(0.4)
            _click_first(page, [
                "button[type='submit']",
                "button:has-text('Confirmar')",
                "button:has-text('Verificar')",
                "button:has-text('Continuar')",
                "button:has-text('Continue')",
            ])
            time.sleep(2)
            return True
        except Exception:
            continue
    return False


def _esperar_otp(page, user_id: str, timeout_seg: int = 600) -> bool:
    """
    Detecta si Indeed pide OTP y lo espera desde dos fuentes:
      1. BigQuery — el usuario lo ingreso en el frontend web (flujo produccion)
      2. stdin    — fallback para tests locales en terminal

    Pone indeed_otp_pending=True en BigQuery para que el frontend
    muestre el input al usuario. Cuando el usuario envia el codigo,
    el frontend llama bq.submit_indeed_otp() y esta funcion lo toma.
    """
    # Esperar hasta 5 segundos para que aparezca la pantalla OTP
    for _ in range(10):
        if _hay_otp_en_pantalla(page):
            break
        time.sleep(0.5)
    else:
        return True  # Indeed no pidio OTP

    print("  [indeed] Pantalla de verificacion OTP detectada")

    import bq
    bq.set_indeed_otp_pending(user_id, True)
    print(f"  [indeed] indeed_otp_pending=True → frontend debe mostrar input de OTP al usuario")
    print(f"  [indeed] Esperando codigo (hasta {timeout_seg // 60} min)...")

    interval = 5
    elapsed  = 0
    while elapsed < timeout_seg:
        # 1. BigQuery (produccion — usuario ingreso en el frontend)
        otp = bq.get_indeed_otp(user_id)
        if otp:
            print(f"  [indeed] OTP recibido desde BigQuery")
            if _ingresar_otp_en_pagina(page, otp):
                bq.set_indeed_otp_pending(user_id, False)
                if not _hay_otp_en_pantalla(page):
                    print("  [indeed] OTP aceptado")
                    return True
                print("  [indeed] OTP rechazado, esperando nuevo codigo...")
                bq.set_indeed_otp_pending(user_id, True)

        # 2. stdin (test local — solo cuando hay terminal interactiva)
        if sys.stdin.isatty():
            try:
                import msvcrt
                # Revisar si hay tecla presionada sin bloquear
                if msvcrt.kbhit():
                    chars = []
                    while msvcrt.kbhit():
                        c = msvcrt.getwche()
                        if c in ('\r', '\n'):
                            break
                        chars.append(c)
                    otp_local = "".join(chars).strip()
                    if otp_local and _ingresar_otp_en_pagina(page, otp_local):
                        bq.set_indeed_otp_pending(user_id, False)
                        if not _hay_otp_en_pantalla(page):
                            return True
            except Exception:
                pass

        time.sleep(interval)
        elapsed += interval

    bq.set_indeed_otp_pending(user_id, False)
    print("  [indeed] Timeout esperando OTP")
    return False


# ── función principal ─────────────────────────────────────────────────────────

def crear_cuenta_indeed(user_id: str, user: dict) -> bool:
    """
    Crea una cuenta en Indeed para el usuario dado.

    Usa el email real del usuario (user['EMAIL']) porque Indeed requiere
    verificación por código OTP — no podemos crear cuentas con emails falsos.

    Args:
        user_id : ID del usuario en BigQuery (ej: 'jobs2')
        user    : dict con EMAIL, NOMBRE (y opcionalmente CELULAR)

    Returns:
        True si la cuenta se creó o ya existía.
    """
    import bq

    # Si ya tiene cuenta guardada, no crear otra
    cuenta = bq.get_portal_account(user_id, PORTAL_ID)
    if cuenta:
        print(f"  [indeed] Cuenta ya existente para {user_id}: {cuenta['email']}")
        return True

    email = str(user.get("EMAIL") or user.get("email") or "").strip()
    if not email:
        print(f"  [indeed] ERROR: el usuario {user_id} no tiene EMAIL definido")
        return False

    nombre_completo = str(user.get("NOMBRE") or user.get("nombre") or "")
    partes   = nombre_completo.strip().split()
    nombre   = partes[0] if partes else "Usuario"
    apellido = " ".join(partes[1:]) if len(partes) > 1 else ""

    clave = _generar_clave()

    print(f"  [indeed] Creando cuenta para {user_id}")
    print(f"           Email   : {email}")
    print(f"           Nombre  : {nombre} {apellido}")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=["--disable-blink-features=AutomationControlled", "--window-size=1280,800"],
        )
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-CL",
        )
        page = ctx.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )

        try:
            page.goto(REGISTER_URL, wait_until="domcontentloaded", timeout=30_000)
            time.sleep(random.uniform(2, 3))

            print(f"  [indeed] URL inicial: {page.url}")

            # ── paso 1: email ─────────────────────────────────────────────────
            email_filled = False
            for sel in ["input[name='__email']", "input[type='email']", "input[id*='email']"]:
                try:
                    page.locator(sel).first.wait_for(timeout=5000)
                    _human_type(page, sel, email)
                    email_filled = True
                    print(f"  [indeed] Email ingresado")
                    break
                except Exception:
                    continue

            if not email_filled:
                print(f"  [indeed] ERROR: no se encontró campo de email. URL: {page.url}")
                page.screenshot(path="indeed_register_debug.png")
                browser.close()
                return False

            _click_first(page, [
                "button[type='submit']",
                "button:has-text('Continuar')",
                "button:has-text('Continue')",
                "button:has-text('Crear cuenta')",
            ])
            time.sleep(random.uniform(1.5, 2.5))

            # ── paso 2: contraseña ────────────────────────────────────────────
            pwd_filled = False
            for sel in ["input[name='__password']", "input[type='password']"]:
                try:
                    page.locator(sel).first.wait_for(timeout=5000)
                    _human_type(page, sel, clave)
                    pwd_filled = True
                    print(f"  [indeed] Contraseña ingresada")
                    break
                except Exception:
                    continue

            if pwd_filled:
                # Confirmar contraseña si hay segundo campo
                pwd_fields = page.locator("input[type='password']").all()
                if len(pwd_fields) >= 2:
                    pwd_fields[1].fill(clave)

                _click_first(page, [
                    "button[type='submit']",
                    "button:has-text('Continuar')",
                    "button:has-text('Crear cuenta')",
                    "button:has-text('Registrarse')",
                ])
                time.sleep(random.uniform(1.5, 2.5))

            # ── paso 3: nombre ────────────────────────────────────────────────
            for (kws, val) in [
                (["first", "nombre", "name"], nombre),
                (["last", "apellido", "surname"], apellido),
            ]:
                for sel in [f"input[name*='{k}']" for k in kws] + [f"input[placeholder*='{k}']" for k in kws]:
                    try:
                        page.locator(sel).first.wait_for(timeout=2000)
                        page.locator(sel).first.fill(val)
                        break
                    except Exception:
                        continue

            time.sleep(0.5)
            _click_first(page, [
                "button[type='submit']",
                "button:has-text('Continuar')",
                "button:has-text('Crear cuenta')",
            ], timeout=3000)
            time.sleep(2)

            # ── paso 4: OTP de verificacion ──────────────────────────────────
            _esperar_otp(page, user_id)
            time.sleep(2)

            # ── paso 5: verificar resultado ───────────────────────────────────
            url_final = page.url
            content   = page.content().lower()

            ya_existe = any(s in content for s in [
                "ya existe", "already registered", "email already in use",
                "ya está registrado", "este correo ya",
            ])
            exito = any(s in content for s in [
                "bienvenido", "welcome", "dashboard", "mis aplicaciones",
                "my applications", "crear cv", "completar perfil", "profile",
            ]) or ("register" not in url_final and "login" not in url_final)

            if ya_existe:
                print(f"  [indeed] Email ya registrado — guardando credencial")
                bq.save_portal_account(user_id, PORTAL_ID, email, clave)
                # Guardar cookies también
                raw = ctx.cookies()
                cookies = [{"name": c["name"], "value": c["value"],
                            "domain": c.get("domain", ".indeed.com"),
                            "path": c.get("path", "/")} for c in raw]
                if cookies:
                    bq.save_portal_cookies(user_id, PORTAL_ID, cookies)
                browser.close()
                return True

            if exito:
                print(f"  [indeed] ✅ Cuenta creada para {user_id} ({email})")
                bq.save_portal_account(user_id, PORTAL_ID, email, clave)
                # Guardar cookies de la sesión recién creada
                raw = ctx.cookies()
                cookies = [{"name": c["name"], "value": c["value"],
                            "domain": c.get("domain", ".indeed.com"),
                            "path": c.get("path", "/")} for c in raw]
                if cookies:
                    bq.save_portal_cookies(user_id, PORTAL_ID, cookies)
                    print(f"  [indeed] {len(cookies)} cookies guardadas en BigQuery")
                browser.close()
                return True

            print(f"  [indeed] ⚠ No se pudo confirmar creación. URL: {url_final}")
            page.screenshot(path="indeed_register_result.png")
            browser.close()
            return False

        except Exception as e:
            import traceback
            print(f"  [indeed] Error: {e}")
            traceback.print_exc()
            try:
                page.screenshot(path="indeed_register_error.png")
            except Exception:
                pass
            browser.close()
            return False


# ── integración con get_or_create_account ─────────────────────────────────────

def get_or_create_indeed_account(user_id: str, user: dict) -> dict | None:
    """
    Retorna credenciales Indeed del usuario, creando la cuenta si no existe.
    Patrón idéntico al get_or_create_account de portal_accounts.py.
    """
    import bq
    cuenta = bq.get_portal_account(user_id, PORTAL_ID)
    if cuenta:
        return cuenta

    ok = crear_cuenta_indeed(user_id, user)
    if not ok:
        return None

    return bq.get_portal_account(user_id, PORTAL_ID)


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    os.environ.setdefault(
        "GOOGLE_APPLICATION_CREDENTIALS",
        r"C:\Users\bastian\Desktop\Script_Python\jobs-425301-ba25295bbbd0.json",
    )
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))

    parser = argparse.ArgumentParser(description="Crear cuenta en Indeed para un usuario")
    parser.add_argument("--user",  required=True, help="ID del usuario en BigQuery (ej: jobs2)")
    parser.add_argument("--email", default=None,  help="Override del email (usa el de POSTULA_FACIL si no se pasa)")
    args = parser.parse_args()

    import bq
    users = bq.get_user_by_id(args.user)
    if not users:
        print(f"Usuario {args.user} no encontrado en BigQuery")
        sys.exit(1)

    user = users[0]
    user["NOMBRE"] = "Bastian Alonso Alfaro Lazo"   # desde portal_accounts.__main__
    user["EMAIL"]  = args.email or "bastian.alfaro@gmail.com"

    ok = crear_cuenta_indeed(args.user, user)
    print(f"\nResultado: {'✅ OK' if ok else '❌ FALLÓ'}")
    sys.exit(0 if ok else 1)
