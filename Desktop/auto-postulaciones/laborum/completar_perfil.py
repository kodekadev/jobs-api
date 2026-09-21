"""
Laborum.cl — Completar perfil del candidato.

Flujo:
  1. Login con cookies guardadas o email/password
  2. Ir a /mi-cuenta/datos o /perfil
  3. Subir CV PDF y llenar campos (cargo, resumen)
  4. Guardar cookies actualizadas en BigQuery

Uso:
    from laborum.completar_perfil import completar_perfil_laborum
    ok = completar_perfil_laborum("jobs2", user_dict)
"""
import os
import sys
import re
import tempfile

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bq

BASE_URL   = "https://www.laborum.cl"
LOGIN_URL  = f"{BASE_URL}/login"
SIGNIN_URL = f"{BASE_URL}/signin"
PORTAL_ID  = "laborum"

CV_URL = f"{BASE_URL}/bienvenida?paso=modalidad"


def _download_cv(cv_url: str) -> str | None:
    if not cv_url:
        return None
    try:
        tmp  = tempfile.mkdtemp()
        path = os.path.join(tmp, "CV.pdf")
        if "storage.googleapis.com" in cv_url and "?" not in cv_url:
            from google.cloud import storage as gcs
            raw    = cv_url.replace("https://storage.googleapis.com/", "")
            bucket, blob = raw.split("/", 1)
            gcs.Client().bucket(bucket).blob(blob).download_to_filename(path)
        else:
            import requests
            r = requests.get(cv_url, timeout=30)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
        print(f"  [lab-perfil] CV descargado ({os.path.getsize(path) // 1024} KB)")
        return path
    except Exception as e:
        print(f"  [lab-perfil] CV descarga error: {e}")
        return None


def _aceptar_cookies(page):
    """Acepta el banner de cookies si aparece."""
    for sel in [
        "button:has-text('Aceptar')",
        "button:has-text('Aceptar todo')",
        "button:has-text('Aceptar cookies')",
        "button:has-text('Accept')",
        "button:has-text('Acepto')",
        "[id*='accept'] button",
        "[class*='cookie'] button",
        "[class*='consent'] button",
    ]:
        try:
            btn = page.locator(sel).first
            if btn.count() and btn.is_visible(timeout=2000):
                btn.click()
                page.wait_for_timeout(500)
                print("  [lab-perfil] Cookies aceptadas")
                return
        except Exception:
            pass


def _esta_logueado(page) -> bool:
    cur     = page.url.lower()
    content = page.content().lower()
    if any(s in cur for s in ("login", "signin", "/acceso")):
        return False
    # Post-registro o post-login → sesión activa
    if any(s in cur for s in ("bienvenida", "/postulantes", "/candidato", "/perfil", "/home")):
        return True
    return any(s in content for s in [
        "cerrar sesión", "mi cuenta", "salir", "logout",
        "mis postulaciones", "editar perfil", "cerrar-sesion",
        # Las de arriba ya no existen en el sitio. Estas se verificaron
        # comparando la misma pagina con y sin cookies (ver postular.py).
        "icon-light-notification", "notificaciones", "mi cv",
    ])


def _type_react(page, selector: str, value: str, timeout: int = 5000):
    """Escribe en un input React usando teclado."""
    loc = page.locator(selector).first
    loc.wait_for(state="visible", timeout=timeout)
    loc.click()
    page.keyboard.press("Control+a")
    page.wait_for_timeout(50)
    page.keyboard.type(value, delay=40)


def _fill_react_select(page, container_id: str, value: str):
    """Selecciona un valor en un React Select por id de contenedor."""
    if not value:
        return
    try:
        ctrl = page.locator(f"#{container_id} .select__control")
        ctrl.wait_for(state="visible", timeout=5000)
        ctrl.click()
        page.wait_for_timeout(300)
        page.keyboard.type(str(value), delay=50)
        page.wait_for_timeout(600)
        option = page.locator(".select__menu-list .select__option").first
        option.wait_for(state="visible", timeout=3000)
        option.click()
        page.wait_for_timeout(300)
    except Exception as e:
        print(f"  [lab-perfil] react-select #{container_id} ({value!r}): {e}")


def _login(page, email: str, password: str) -> bool:
    """Login en /signin — mismo flujo que funciona en activar_cuentas_lab.py."""
    # Limpiar cookies para que /signin cargue limpio sin redirects por sesión parcial
    try:
        page.context.clear_cookies()
    except Exception:
        pass
    page.goto(SIGNIN_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    # 1. Abrir formulario de correo
    btn = page.locator(
        "button:has-text('correo electrónico'), button:has-text('Continuar con correo')"
    ).first
    btn.wait_for(state="visible", timeout=10000)
    btn.click()
    page.wait_for_timeout(1500)

    # 2. Escribir email
    _type_react(page, "input[name='email'], input[type='email']", email)
    page.wait_for_timeout(200)

    # 3. Click "Continuar" (paso intermedio que muestra el campo de password)
    try:
        cont = page.locator("button:has-text('Continuar'):visible").first
        if cont.is_visible(timeout=3000):
            cont.click()
            page.wait_for_timeout(1500)
    except Exception:
        pass

    # 4. Password
    _type_react(page, "input[name='password'], input[type='password']", password)
    page.wait_for_timeout(200)

    # 5. Submit
    page.locator("button[type='submit']:visible, #iniciarSesion:visible").first.click()
    page.wait_for_timeout(5000)

    ok = _esta_logueado(page)
    print(f"  [lab-perfil] Login {'OK' if ok else 'FALLÓ'} — {page.url[:80]}")
    return ok


def completar_perfil_laborum(user_id: str, user: dict) -> bool:
    cuenta = bq.get_portal_account(user_id, PORTAL_ID)
    if not cuenta:
        print(f"  [lab-perfil] Sin cuenta para {user_id}")
        return False

    email    = cuenta.get("email", "")
    password = cuenta.get("password", "")

    nombre_completo = str(user.get("NOMBRE") or user.get("nombre") or "")
    profesion = str(user.get("PROFESION") or user.get("profesion") or "")
    resumen   = str(user.get("RESUMEN")   or user.get("resumen")   or "")
    cv_url    = str(user.get("CV_URL")    or user.get("cv_url")    or "")

    # Fecha de nacimiento
    fn_raw = str(user.get("FECHA_NACIMIENTO") or user.get("fecha_nacimiento") or "")
    dia_nac = mes_nac = anio_nac = ""
    if fn_raw:
        parts = re.split(r"[-/]", fn_raw.strip())
        if len(parts) == 3:
            if len(parts[0]) == 4:          # YYYY-MM-DD
                anio_nac, mes_nac, dia_nac = parts
            else:                           # DD/MM/YYYY
                dia_nac, mes_nac, anio_nac = parts
    dia_nac  = (dia_nac.lstrip("0")  or str(user.get("DIA_NAC")  or "")).strip()
    mes_nac  = (mes_nac.lstrip("0")  or str(user.get("MES_NAC")  or "")).strip()
    anio_nac = (anio_nac             or str(user.get("ANIO_NAC") or "")).strip()

    # Género (M/F → Masculino/Femenino)
    _g = str(user.get("GENERO") or user.get("genero") or user.get("SEXO") or "").upper()
    if _g in ("M", "MASCULINO", "HOMBRE"):
        genero_lab = "Masculino"
    elif _g in ("F", "FEMENINO", "MUJER"):
        genero_lab = "Femenino"
    else:
        genero_lab = ""

    # Comuna (primera de UBICACIONES)
    _ub_raw = user.get("UBICACIONES") or user.get("ubicaciones") or ""
    if isinstance(_ub_raw, str) and _ub_raw.strip().startswith("["):
        try:
            import json as _j; _ub_list = _j.loads(_ub_raw)
        except Exception:
            _ub_list = [_ub_raw]
    elif isinstance(_ub_raw, list):
        _ub_list = _ub_raw
    else:
        _ub_list = [_ub_raw] if _ub_raw else []
    comuna = (_ub_list[0].split(",")[0].strip() if _ub_list else "") or ""

    cv_path = _download_cv(cv_url)

    try:
        from playwright.sync_api import sync_playwright
        from portal_accounts import _make_pw_context, _new_stealth_page
    except ImportError:
        print("  [lab-perfil] Playwright no disponible")
        return False

    try:
        with sync_playwright() as pw:
            _, browser, ctx, _ = _make_pw_context(pw)
            page = _new_stealth_page(ctx)

            # Inyectar cookies guardadas
            cookies = bq.get_portal_cookies(user_id, PORTAL_ID)
            if cookies:
                valid = [c for c in cookies if isinstance(c, dict) and "name" in c and "value" in c]
                ctx.add_cookies(valid)
                print(f"  [lab-perfil] {len(valid)} cookies inyectadas")

            # Verificar sesión con home (curriculum da 404 sin login)
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            _aceptar_cookies(page)

            if not _esta_logueado(page):
                if not _login(page, email, password):
                    browser.close()
                    return False

            # Ya logueado — ir al paso de modalidad y elegir Jobi (CV parser IA)
            page.goto(CV_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            _aceptar_cookies(page)
            print(f"  [lab-perfil] Modalidad: {page.url[:80]}")

            # Click en la card "Lectura automática con Jobi"
            jobi = page.locator("h3:has-text('Lectura automática con Jobi')").first
            if jobi.count() and jobi.is_visible(timeout=5000):
                jobi.click()
                page.wait_for_timeout(2000)
                print(f"  [lab-perfil] Click Jobi OK — {page.url[:80]}")
            else:
                # El wizard ya fue iniciado antes — ir directo al paso de cv_parser
                print("  [lab-perfil] Card Jobi no encontrada — navegando directo a cv_parser")
                page.goto(
                    f"{BASE_URL}/bienvenida?paso=cv_parser&flujo=parser",
                    wait_until="domcontentloaded", timeout=30000
                )
                page.wait_for_timeout(2000)
                _aceptar_cookies(page)
                print(f"  [lab-perfil] cv_parser: {page.url[:80]}")
                # Click "Actualizar CV" que aparece en este paso del wizard ya iniciado
                try:
                    actualizar = page.locator("p:has-text('Actualizar CV'), button:has-text('Actualizar CV')").first
                    if actualizar.count() and actualizar.is_visible(timeout=5000):
                        actualizar.click()
                        page.wait_for_timeout(2000)
                        print("  [lab-perfil] Click 'Actualizar CV' OK")
                except Exception as e:
                    print(f"  [lab-perfil] 'Actualizar CV' no encontrado: {e}")
                # Continuar para llegar al upload
                try:
                    cont = page.locator("button:has-text('Continuar')").first
                    if cont.count() and cont.is_visible(timeout=5000):
                        cont.click()
                        page.wait_for_timeout(2000)
                        print("  [lab-perfil] Click 'Continuar' (pre-upload) OK")
                except Exception as e:
                    print(f"  [lab-perfil] 'Continuar' pre-upload no encontrado: {e}")

            # Subir CV
            if cv_path and os.path.exists(cv_path):
                try:
                    for file_sel in ["input[type='file']", "input[accept*='pdf']", "input[accept*='.pdf']"]:
                        file_loc = page.locator(file_sel).first
                        if file_loc.count():
                            page.evaluate(
                                "el => { el.style.display='block'; el.style.visibility='visible'; }",
                                file_loc.element_handle()
                            )
                            file_loc.set_input_files(cv_path)
                            page.wait_for_timeout(1500)
                            print("  [lab-perfil] CV adjuntado — buscando botón Convertir CV...")

                            # Click en "Convertir CV" para que Jobi arranque
                            try:
                                convertir = page.locator("button:has-text('Convertir CV')").first
                                convertir.wait_for(state="visible", timeout=10000)
                                convertir.click()
                                print("  [lab-perfil] Click 'Convertir CV' — Jobi procesando (~60s)...")
                            except Exception as e:
                                print(f"  [lab-perfil] No encontró 'Convertir CV': {e}")

                            # Esperar hasta 90s a que Jobi termine (aparece botón "Continuar")
                            try:
                                page.locator("button:has-text('Continuar')").first.wait_for(
                                    state="visible", timeout=90000
                                )
                                print("  [lab-perfil] Jobi terminó")
                            except Exception:
                                print("  [lab-perfil] Timeout esperando Jobi — continuando igual")

                            # Loop de pasos wizard: llenar datos personales + "Guardar y continuar" / "Continuar"
                            for paso in range(15):
                                # Esperar a que aparezca el botón de avance del paso
                                try:
                                    page.locator(
                                        "button:has-text('Guardar y continuar'), "
                                        "button:has-text('Continuar')"
                                    ).first.wait_for(state="visible", timeout=10000)
                                except Exception:
                                    pass
                                page.wait_for_timeout(1000)

                                # Paso datos personales: detectar por selector #day o título
                                if page.locator("#day, h3:has-text('Revisa tus datos')").count():
                                    print(f"  [lab-perfil] Paso {paso+1}: datos personales — llenando...")
                                    if dia_nac:
                                        _fill_react_select(page, "day", dia_nac)
                                    if mes_nac:
                                        _fill_react_select(page, "month", mes_nac)
                                    if anio_nac:
                                        _fill_react_select(page, "year", anio_nac)
                                    if genero_lab:
                                        _fill_react_select(page, "select-genero", genero_lab)
                                    if comuna:
                                        _fill_react_select(page, "select-localidad", comuna)
                                    page.wait_for_timeout(800)

                                # Si hay opción de reemplazar contenido con el del CV nuevo → elegirla
                                reemplazar = page.locator("button:has-text('Reemplazar contenido')").first
                                if reemplazar.count() and reemplazar.is_visible(timeout=2000):
                                    print(f"  [lab-perfil] Paso {paso+1}: 'Reemplazar contenido' — {page.url[:60]}")
                                    reemplazar.click()
                                    continue

                                # Prioridad: "Guardar y continuar" > "Continuar"
                                guardar = page.locator("button:has-text('Guardar y continuar')").first
                                if guardar.count() and guardar.is_visible(timeout=3000):
                                    print(f"  [lab-perfil] Paso {paso+1}: 'Guardar y continuar' — {page.url[:60]}")
                                    guardar.click()
                                    continue
                                cont = page.locator("button:has-text('Continuar')").first
                                if cont.count() and cont.is_visible(timeout=3000):
                                    print(f"  [lab-perfil] Paso {paso+1}: 'Continuar' — {page.url[:60]}")
                                    cont.click()
                                    continue
                                print(f"  [lab-perfil] Sin más pasos wizard — {page.url[:80]}")
                                break
                            break
                except Exception as e:
                    print(f"  [lab-perfil] CV upload error: {e}")

            # Llenar cargo/profesión si hay campo
            if profesion:
                for sel in ["input[name='cargo'], input[name='titulo'], input[name='puesto']",
                            "input[placeholder*='cargo' i]", "input[placeholder*='profesión' i]"]:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() and loc.is_visible(timeout=2000):
                            _type_react(page, sel, profesion)
                            page.wait_for_timeout(200)
                            break
                    except Exception:
                        pass

            # Llenar resumen/descripción
            if resumen:
                for sel in ["textarea[name='resumen']", "textarea[name='descripcion']",
                            "textarea[name='about']", "textarea[placeholder*='resumen' i]"]:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() and loc.is_visible(timeout=2000):
                            loc.click()
                            page.wait_for_timeout(50)
                            page.keyboard.type(resumen[:800], delay=10)
                            page.wait_for_timeout(200)
                            break
                    except Exception:
                        pass


            new_cookies = ctx.cookies()
            bq.save_portal_cookies(user_id, PORTAL_ID, new_cookies, cv_completo=True)
            print(f"  [lab-perfil] Perfil completado para {user_id} ({len(new_cookies)} cookies)")
            browser.close()
            return True

    except Exception as e:
        import traceback
        print(f"  [lab-perfil] Error: {e}")
        traceback.print_exc()
        return False
    finally:
        if cv_path and os.path.exists(cv_path):
            try:
                os.unlink(cv_path)
            except Exception:
                pass


if __name__ == "__main__":
    os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", r"C:\Users\bastian\.secrets\google\credenciales.json")
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
    users = bq.get_active_users()
    for u in users[:1]:
        completar_perfil_laborum(u["ID_USUARIO"], u)
