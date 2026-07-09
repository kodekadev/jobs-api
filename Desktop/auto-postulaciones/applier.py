"""
Postulación automática via Playwright.

Flujo: scraper encuentra empleo → emailer intenta email de contacto
→ si no hay email → applier abre el formulario del portal y lo llena.

Portales soportados:
  - Trabajando.cl
  - Computrabajo.cl
  - ChileTrabajos.cl
  - Indeed (Easy Apply)
  - LinkedIn (Easy Apply — solo si está disponible sin CAPTCHA)
"""

import os
import re
import time
import random
import tempfile
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# En Cloud Run siempre headless. Localmente muestra el browser por defecto.
_in_cloud_run = bool(os.environ.get("K_SERVICE"))
HEADLESS = os.environ.get("PLAYWRIGHT_HEADLESS", "1" if _in_cloud_run else "0") != "0"

# Respuestas estándar para preguntas comunes de formularios
_DISPONIBILIDAD = ["Inmediata", "Inmediato", "De inmediato", "Disponible de inmediato"]
_PRETENSION_DEFAULT = "A convenir"


def _download_cv(cv_url: str) -> str | None:
    """Descarga CV a un archivo temporal, retorna la ruta.
    Soporta URLs públicas y URLs de GCS privadas (gs:// o storage.googleapis.com/...)."""
    if not cv_url:
        return None

    # Intentar con cliente GCS si la URL es de Google Cloud Storage
    if "storage.googleapis.com" in cv_url or cv_url.startswith("gs://"):
        try:
            from google.cloud import storage as gcs
            client = gcs.Client()
            # Parsear bucket y blob del URL
            # https://storage.googleapis.com/BUCKET/PATH  →  bucket=BUCKET, blob=PATH
            if cv_url.startswith("gs://"):
                parts = cv_url[5:].split("/", 1)
            else:
                path = cv_url.replace("https://storage.googleapis.com/", "")
                parts = path.split("/", 1)
            bucket_name, blob_path = parts[0], parts[1]
            bucket = client.bucket(bucket_name)
            blob   = bucket.blob(blob_path)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            blob.download_to_filename(tmp.name)
            tmp.close()
            print(f"  CV descargado desde GCS: {blob_path}")
            return tmp.name
        except Exception as e:
            print(f"  ⚠ GCS download falló ({e}), intentando HTTP…")

    # Fallback: descarga HTTP pública
    try:
        req = urllib.request.Request(cv_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp.write(data)
        tmp.close()
        return tmp.name
    except Exception as e:
        print(f"  ⚠ No se pudo descargar CV: {e}")
        return None


def _clean_phone(phone: str) -> str:
    """Normaliza teléfono chileno: +56912345678 → 912345678."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("56") and len(digits) >= 11:
        return digits[2:]
    return digits[-9:] if len(digits) >= 9 else digits


def _fill_text(page, selector: str, value: str, clear: bool = True) -> bool:
    """Intenta rellenar un campo de texto. Retorna True si tuvo éxito."""
    try:
        el = page.locator(selector).first
        el.wait_for(timeout=3000)
        if clear:
            el.triple_click()
        el.fill(value)
        return True
    except Exception:
        return False


def _click_if_exists(page, selector: str, timeout: int = 3000) -> bool:
    try:
        page.locator(selector).first.wait_for(timeout=timeout)
        page.locator(selector).first.click()
        return True
    except Exception:
        return False


# ─── COMPUTRABAJO.CL ──────────────────────────────────────────────────────────

def apply_computrabajo(user: dict, job_url: str) -> bool:
    """
    Postula a un empleo en Computrabajo.cl.
    Flujo: job page → "Postularme" → modal con nombre/email/CV → enviar.
    """
    nombre     = user.get("NOMBRE", "")
    email      = user.get("EMAIL", "")
    celular    = _clean_phone(user.get("CELULAR", ""))
    pretension = str(user.get("pretension_general") or _PRETENSION_DEFAULT)
    cv_path    = _download_cv(user.get("cv_url") or user.get("CV_URL", ""))

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=HEADLESS)
            ctx     = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = ctx.new_page()
            page.goto(job_url, timeout=20000, wait_until="domcontentloaded")
            time.sleep(random.uniform(1, 2))

            # Buscar botón "Postularme" / "Postular"
            btn = None
            for sel in ["text=Postularme", "text=Postular", "[data-cy='apply-btn']",
                        "a.btn-apply", "button.btn-apply", ".js-btn-apply"]:
                if _click_if_exists(page, sel, timeout=3000):
                    btn = sel
                    break

            if not btn:
                print(f"    ⚠ Computrabajo: no encontré botón postular en {job_url[:60]}")
                browser.close()
                return False

            time.sleep(random.uniform(1, 2))

            # Llenar formulario modal
            _fill_text(page, "input[name='name'], input[placeholder*='ombre']", nombre)
            _fill_text(page, "input[name='email'], input[type='email']", email)
            _fill_text(page, "input[name='phone'], input[placeholder*='elefono'], input[placeholder*='Celular']", celular)
            _fill_text(page, "input[placeholder*='retensión'], input[placeholder*='alario']", pretension)

            # Subir CV
            if cv_path:
                try:
                    fc = page.locator("input[type='file']").first
                    fc.set_input_files(cv_path)
                except Exception:
                    pass

            # Enviar
            submitted = False
            for sel in ["button[type='submit']", "input[type='submit']",
                        "text=Enviar", "text=Postular", "text=Enviar postulación"]:
                if _click_if_exists(page, sel, timeout=3000):
                    submitted = True
                    break

            time.sleep(2)

            # Verificar confirmación
            success = False
            for signal in ["Postulación enviada", "Te has postulado", "Gracias", "éxito",
                           "postulación fue enviada", "aplicación enviada"]:
                if signal.lower() in page.content().lower():
                    success = True
                    break

            if not success and submitted:
                success = True  # Asumimos éxito si el envío no dio error visible

            browser.close()
            return success

    except PWTimeout:
        print(f"    ⚠ Computrabajo timeout en {job_url[:60]}")
        return False
    except Exception as e:
        print(f"    ⚠ Computrabajo error: {e}")
        return False
    finally:
        if cv_path and Path(cv_path).exists():
            Path(cv_path).unlink(missing_ok=True)


# ─── TRABAJANDO.CL ────────────────────────────────────────────────────────────

def _login_trabajando(page, email: str, password: str) -> bool:
    """Hace login en trabajando.cl si no hay sesión activa."""
    try:
        # Si ya hay sesión activa no hace nada
        if "mi-curriculum" in page.url or "dashboard" in page.url:
            return True
        page.goto("https://www.trabajando.cl/ingresa", wait_until="domcontentloaded", timeout=20000)
        time.sleep(random.uniform(1, 1.5))
        _fill_text(page, 'input[type="email"], input[name="email"]', email)
        _fill_text(page, 'input[type="password"], input[name="password"]', password)
        _click_if_exists(page, "button:has-text('Ingresar'), button[type='submit']")
        time.sleep(2)
        return True
    except Exception as e:
        print(f"    ! Login Trabajando error: {e}")
        return False


def apply_trabajando(user: dict, job_url: str, credentials: dict | None = None) -> bool:
    """
    Postula a un empleo en Trabajando.cl.
    Flujo: job page → "Postular" → formulario → enviar.
    """
    nombre     = user.get("NOMBRE", "")
    email      = user.get("EMAIL", "")
    celular    = _clean_phone(user.get("CELULAR", ""))
    pretension = str(user.get("pretension_general") or _PRETENSION_DEFAULT)
    cv_path    = _download_cv(user.get("cv_url") or user.get("CV_URL", ""))

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=HEADLESS)
            ctx     = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = ctx.new_page()

            # Login previo si tenemos credenciales
            if credentials:
                _login_trabajando(page, credentials["email"], credentials["password"])

            page.goto(job_url, timeout=20000, wait_until="domcontentloaded")
            time.sleep(random.uniform(1, 2))

            # Botón postular
            clicked = False
            for sel in ["text=Postular", ".btn-postular", "#btn-postular",
                        "a[href*='postular']", "button[data-action='postular']"]:
                if _click_if_exists(page, sel, timeout=3000):
                    clicked = True
                    break

            if not clicked:
                print(f"    ⚠ Trabajando: no encontré botón postular en {job_url[:60]}")
                browser.close()
                return False

            time.sleep(random.uniform(1.5, 2.5))

            # Formulario
            _fill_text(page, "input#nombre, input[name='nombre'], input[placeholder*='ombre']", nombre)
            _fill_text(page, "input#email, input[name='email'], input[type='email']", email)
            _fill_text(page, "input#telefono, input[name='telefono'], input[placeholder*='elefono']", celular)
            _fill_text(page, "input[name='pretension'], input[placeholder*='retensión']", pretension)
            _fill_text(page, "textarea[name='carta'], textarea[placeholder*='carta'], textarea[placeholder*='resumen']",
                       user.get("resumen") or "Me interesa la posición. Adjunto CV para revisión.")

            if cv_path:
                try:
                    page.locator("input[type='file']").first.set_input_files(cv_path)
                except Exception:
                    pass

            submitted = False
            for sel in ["button[type='submit']", "input[type='submit']",
                        "text=Enviar", "text=Postular ahora", ".btn-submit"]:
                if _click_if_exists(page, sel, timeout=3000):
                    submitted = True
                    break

            time.sleep(2)

            success = submitted
            for signal in ["Gracias", "postulación enviada", "Te contactaremos", "éxito"]:
                if signal.lower() in page.content().lower():
                    success = True
                    break

            browser.close()
            return success

    except PWTimeout:
        print(f"    ⚠ Trabajando timeout en {job_url[:60]}")
        return False
    except Exception as e:
        print(f"    ⚠ Trabajando error: {e}")
        return False
    finally:
        if cv_path and Path(cv_path).exists():
            Path(cv_path).unlink(missing_ok=True)


# ─── CHILETRABAJOS.CL ─────────────────────────────────────────────────────────

def apply_chiletrabajos(user: dict, job_url: str) -> bool:
    """Postula a un empleo en ChileTrabajos.cl."""
    nombre     = user.get("NOMBRE", "")
    email      = user.get("EMAIL", "")
    celular    = _clean_phone(user.get("CELULAR", ""))
    cv_path    = _download_cv(user.get("cv_url") or user.get("CV_URL", ""))

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=HEADLESS)
            ctx     = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = ctx.new_page()
            page.goto(job_url, timeout=20000, wait_until="domcontentloaded")
            time.sleep(random.uniform(1, 2))

            for sel in ["text=Postular", "text=Postularme", ".btn-apply", "#apply-btn"]:
                if _click_if_exists(page, sel, timeout=3000):
                    break

            time.sleep(1.5)

            _fill_text(page, "input[name='name'], input[name='nombre'], input[type='text']", nombre)
            _fill_text(page, "input[type='email']", email)
            _fill_text(page, "input[name='phone'], input[name='telefono']", celular)

            if cv_path:
                try:
                    page.locator("input[type='file']").first.set_input_files(cv_path)
                except Exception:
                    pass

            submitted = False
            for sel in ["button[type='submit']", "text=Enviar", "text=Postular"]:
                if _click_if_exists(page, sel, timeout=3000):
                    submitted = True
                    break

            time.sleep(2)
            browser.close()
            return submitted

    except Exception as e:
        print(f"    ⚠ ChileTrabajos error: {e}")
        return False
    finally:
        if cv_path and Path(cv_path).exists():
            Path(cv_path).unlink(missing_ok=True)


# ─── LINKEDIN ─────────────────────────────────────────────────────────────────

def apply_linkedin(user: dict, job_url: str, direct_url: str = "",
                   cookies: list[dict] | None = None) -> bool:
    """
    Postula a un empleo de LinkedIn.
    Estrategia:
      1. Si hay direct_url (ATS externo) → intenta postular ahí directamente
      2. Si hay cookies de sesión → LinkedIn Easy Apply modal
      3. Sin cookies → retorna False (requiere login previo)
    """
    nombre     = user.get("NOMBRE") or user.get("nombre") or ""
    email      = user.get("EMAIL") or user.get("email") or ""
    celular    = _clean_phone(user.get("CELULAR") or user.get("celular") or "")
    cv_path    = _download_cv(user.get("cv_url") or user.get("CV_URL") or "")

    if cookies is None:
        uid = user.get("ID_USUARIO") or user.get("id_usuario", "")
        if uid:
            try:
                import bq as _bq
                cookies = _bq.get_portal_cookies(uid, "linkedin") or []
            except Exception:
                cookies = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=HEADLESS,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="es-CL",
            )
            if cookies:
                try:
                    ctx.add_cookies(cookies)
                except Exception:
                    pass

            page = ctx.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )

            # ── 1. Intentar URL directa (ATS externo) ────────────────────────
            if direct_url and direct_url.startswith("http") and "linkedin.com" not in direct_url:
                try:
                    page.goto(direct_url, timeout=20000, wait_until="domcontentloaded")
                    time.sleep(random.uniform(1.5, 2.5))
                    content = page.content().lower()
                    # Buscar campos de aplicación — si tiene formulario, llenarlo
                    has_form = any(s in content for s in ["input type", "textarea", "apply", "postul"])
                    if has_form:
                        _fill_text(page, "input[name*='name'], input[placeholder*='ombre'], input[id*='name']", nombre)
                        _fill_text(page, "input[type='email'], input[name*='email']", email)
                        _fill_text(page, "input[type='tel'], input[name*='phone'], input[name*='phone']", celular)
                        if cv_path:
                            try:
                                page.locator("input[type='file']").first.set_input_files(cv_path)
                                time.sleep(1)
                            except Exception:
                                pass
                        _answer_common_questions(page, user)
                        for sel in ["button[type='submit']", "input[type='submit']",
                                    "text=Submit", "text=Apply", "text=Postular", "text=Enviar"]:
                            if _click_if_exists(page, sel, timeout=3000):
                                time.sleep(2)
                                browser.close()
                                print(f"    [linkedin] Postulado via URL directa: {direct_url[:60]}")
                                return True
                except Exception:
                    pass

            # ── 2. LinkedIn Easy Apply (requiere cookies de sesión) ───────────
            if not cookies:
                browser.close()
                print(f"    [linkedin] Sin cookies de sesión — no se puede aplicar")
                return False

            page.goto(job_url, timeout=30000, wait_until="domcontentloaded")
            time.sleep(random.uniform(2, 3))

            # Verificar que estamos logueados (no redirigió a login)
            if "linkedin.com/login" in page.url or "authwall" in page.url:
                print(f"    [linkedin] Cookies expiradas — redirigió a login")
                browser.close()
                return False

            # Buscar botón Easy Apply
            easy_apply_clicked = False
            for sel in [
                "button.jobs-apply-button",
                "button[data-control-name='jobdetails_topcard_inapply']",
                "button:has-text('Easy Apply')",
                "button:has-text('Solicitud sencilla')",
                "button:has-text('Postulación simplificada')",
                ".jobs-apply-button",
            ]:
                try:
                    page.locator(sel).first.wait_for(timeout=3000)
                    page.locator(sel).first.click()
                    easy_apply_clicked = True
                    break
                except Exception:
                    continue

            if not easy_apply_clicked:
                print(f"    [linkedin] Sin botón Easy Apply en {job_url[:60]}")
                browser.close()
                return False

            time.sleep(2)

            # ── Wizard multi-paso ─────────────────────────────────────────────
            submitted = False
            for step in range(8):
                time.sleep(1.5)
                content = page.content().lower()

                # ¿Ya llegamos a "review"?
                if any(s in content for s in ["review your application", "revisar solicitud",
                                               "submit application", "enviar solicitud"]):
                    for sel in ["button:has-text('Submit application')",
                                "button:has-text('Enviar solicitud')",
                                "button:has-text('Submit')"]:
                        if _click_if_exists(page, sel, timeout=4000):
                            submitted = True
                            break
                    break

                # ¿Hay modal de error/CAPTCHA? → abortar
                if any(s in content for s in ["captcha", "verification", "verificación"]):
                    print(f"    [linkedin] CAPTCHA detectado — abortando")
                    break

                # Llenar campos de contacto si aparecen
                _fill_text(page, "input[id*='phoneNumber'], input[name*='phone']", celular)
                _fill_text(page, "input[id*='email'], input[name*='email']", email)

                # Subir CV si hay input file
                if cv_path:
                    try:
                        fi = page.locator("input[type='file']").first
                        fi.wait_for(timeout=1000)
                        fi.set_input_files(cv_path)
                        time.sleep(1)
                    except Exception:
                        pass

                # Responder preguntas comunes
                _answer_common_questions(page, user)

                # Avanzar al siguiente paso
                advanced = False
                for sel in ["button:has-text('Next')", "button:has-text('Siguiente')",
                            "button:has-text('Continue')", "button:has-text('Continuar')",
                            "button[aria-label*='Next']", "button[aria-label*='Continue']"]:
                    if _click_if_exists(page, sel, timeout=2000):
                        advanced = True
                        break

                if not advanced:
                    # Intentar submit directo
                    for sel in ["button:has-text('Submit application')",
                                "button:has-text('Enviar solicitud')"]:
                        if _click_if_exists(page, sel, timeout=2000):
                            submitted = True
                            break
                    break

            time.sleep(2)
            # Confirmar éxito buscando señal en la página
            if not submitted:
                content = page.content().lower()
                submitted = any(s in content for s in [
                    "application submitted", "solicitud enviada", "applied",
                    "you've applied", "application was sent",
                ])
            browser.close()
            return submitted

    except Exception as e:
        print(f"    ⚠ LinkedIn error: {e}")
        return False
    finally:
        if cv_path and Path(cv_path).exists():
            Path(cv_path).unlink(missing_ok=True)


# ─── INDEED ───────────────────────────────────────────────────────────────────

def apply_indeed(user: dict, job_url: str, cookies: list[dict] | None = None) -> bool:
    """
    Intenta postular via Indeed Easy Apply.
    Solo funciona en empleos con botón 'Aplicar fácilmente'.

    Args:
        user    : dict con NOMBRE, EMAIL, CELULAR, CV_URL y campos de perfil
        job_url : URL del aviso en cl.indeed.com
        cookies : lista de cookies de sesión Indeed (cargadas desde BigQuery).
                  Si se pasan, el browser navega ya autenticado.
    """
    nombre  = user.get("NOMBRE", "")
    email   = user.get("EMAIL", "")
    celular = _clean_phone(user.get("CELULAR", ""))
    cv_path = _download_cv(user.get("cv_url") or user.get("CV_URL", ""))

    # Intentar cargar cookies desde BigQuery si no se pasaron explícitamente
    if cookies is None:
        uid = user.get("ID_USUARIO") or user.get("id_usuario", "")
        if uid:
            try:
                import bq as _bq
                cookies = _bq.get_portal_cookies(uid, "indeed") or []
            except Exception:
                cookies = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=HEADLESS,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="es-CL",
            )
            # Inyectar cookies de sesión si existen
            if cookies:
                try:
                    ctx.add_cookies(cookies)
                    print(f"  Indeed: {len(cookies)} cookies de sesión inyectadas")
                except Exception as e:
                    print(f"  ⚠ No se pudieron inyectar cookies: {e}")

            page = ctx.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            page.goto(job_url, timeout=30000, wait_until="domcontentloaded")
            time.sleep(random.uniform(1.5, 3))

            # Verificar si tiene Easy Apply
            has_easy = False
            for sel in ["text=Aplicar fácilmente", "text=Apply now", ".ia-IndeedApplyButton",
                        "[data-testid='indeedApplyButton']"]:
                try:
                    page.locator(sel).first.wait_for(timeout=2000)
                    has_easy = True
                    page.locator(sel).first.click()
                    break
                except Exception:
                    continue

            if not has_easy:
                browser.close()
                return False

            time.sleep(2)

            # Llenar pasos del formulario de Indeed
            for _ in range(6):  # máximo 6 pasos del wizard
                content = page.content().lower()
                if any(s in content for s in ["review your application", "revisión", "enviar solicitud"]):
                    break

                _fill_text(page, "input[name='name.first'], input[placeholder*='nombre']", nombre.split()[0] if nombre else "")
                _fill_text(page, "input[name='name.last'], input[placeholder*='apellido']", nombre.split()[-1] if nombre else "")
                _fill_text(page, "input[type='email']", email)
                _fill_text(page, "input[type='tel'], input[name='phoneNumber']", celular)

                if cv_path:
                    try:
                        page.locator("input[type='file']").first.set_input_files(cv_path)
                        time.sleep(1)
                    except Exception:
                        pass

                # Responder preguntas sí/no o texto libre
                _answer_common_questions(page, user)

                # Avanzar al siguiente paso
                advanced = False
                for sel in ["text=Continuar", "text=Continue", "text=Next",
                            "button[type='submit']", "[data-testid='continue-button']"]:
                    if _click_if_exists(page, sel, timeout=2000):
                        advanced = True
                        break

                if not advanced:
                    break
                time.sleep(1.5)

            # Enviar
            submitted = False
            for sel in ["text=Enviar solicitud", "text=Submit application",
                        "text=Submit", "[data-testid='submit-button']"]:
                if _click_if_exists(page, sel, timeout=3000):
                    submitted = True
                    break

            time.sleep(2)
            browser.close()
            return submitted

    except Exception as e:
        print(f"    ⚠ Indeed error: {e}")
        return False
    finally:
        if cv_path and Path(cv_path).exists():
            Path(cv_path).unlink(missing_ok=True)


# ─── RESPUESTAS AUTOMÁTICAS A PREGUNTAS COMUNES ───────────────────────────────

def _answer_common_questions(page, user: dict) -> None:
    """
    Intenta responder preguntas frecuentes de formularios.
    Usa el perfil del usuario para dar respuestas coherentes.
    """
    pretension = str(user.get("pretension_general") or "")
    experiencia = str(user.get("experiencia") or user.get("EXPERIENCIA") or "0")

    # Selects / dropdowns comunes
    for label_text, value in [
        ("disponibilidad", "Inmediata"),
        ("jornada", "Full time"),
        ("modalidad", "Presencial"),
    ]:
        try:
            label = page.locator(f"label:has-text('{label_text}')").first
            select_id = label.get_attribute("for")
            if select_id:
                page.select_option(f"#{select_id}", label=value)
        except Exception:
            pass

    # Inputs numéricos de pretensión
    for sel in ["input[name*='pretension'], input[name*='salary'], input[placeholder*='retensión']"]:
        _fill_text(page, sel, pretension)

    # Años de experiencia
    for sel in ["input[name*='experiencia'], input[name*='experience'], input[placeholder*='xperiencia']"]:
        _fill_text(page, sel, experiencia)

    # Radio buttons — elegir primeras opciones lógicas
    try:
        radios = page.locator("input[type='radio']").all()
        for radio in radios[:3]:
            label_el = page.locator(f"label[for='{radio.get_attribute('id')}']")
            label_text = (label_el.inner_text() or "").lower()
            # Preferir opciones positivas
            if any(w in label_text for w in ["sí", "si", "yes", "inmediata", "full"]):
                radio.click()
                break
    except Exception:
        pass


# ─── FUNCIÓN PRINCIPAL ────────────────────────────────────────────────────────

def apply_via_form(user: dict, job: dict, credentials: dict | None = None,
                   trabajando_driver=None, trabajando_page=None,
                   chiletrabajos_driver=None, chiletrabajos_page=None,
                   linkedin_cookies: list | None = None) -> "dict | bool":
    """
    Intenta postular al empleo usando el formulario del portal.
    Para Trabajando.cl usa Playwright (page) si está disponible, Selenium como fallback.
    Para ChileTrabajos usa Playwright (page) si está disponible.
    """
    from portal_accounts import apply_trabajando_selenium, apply_trabajando_playwright

    url        = job.get("link", "")
    fuente     = job.get("fuente", "").lower()
    direct_url = job.get("direct_url", "")

    if not url:
        return False

    try:
        if "trabajando" in url or fuente == "trabajando":
            resumen    = user.get("resumen") or ""
            job_title  = job.get("titulo", "")
            if trabajando_page:
                ok = apply_trabajando_playwright(trabajando_page, url, user=user, resumen=resumen, job_title=job_title)
            elif trabajando_driver:
                ok = apply_trabajando_selenium(trabajando_driver, url, user=user, resumen=resumen)
            else:
                print(f"  ⚠ Trabajando: sin sesion activa para {url[:60]}")
                return False

        elif "computrabajo" in url or fuente == "computrabajo":
            ok = apply_computrabajo(user, url)

        elif "chiletrabajos" in url or fuente == "chiletrabajos":
            if chiletrabajos_page:
                from chiletrabajos.postular import _postular_empleo_pw
                ok = _postular_empleo_pw(chiletrabajos_page, url, user, job.get("titulo", ""))
            else:
                ok = apply_chiletrabajos(user, url)

        elif fuente == "linkedin" or "linkedin.com" in url:
            ok = apply_linkedin(user, url, direct_url=direct_url, cookies=linkedin_cookies)

        else:
            print(f"  ⚠ Fuente sin handler de postulación: [{fuente}] {job.get('titulo', '')[:50]}")
            return False

        success = ok.get("ok", False) if isinstance(ok, dict) else bool(ok)
        if success:
            print(f"  ✓ Postulado [{fuente}] → {job.get('titulo', '')[:50]}")
        return ok if isinstance(ok, dict) else success

    except Exception as e:
        print(f"  ⚠ apply_via_form [{fuente}]: {e}")
        return False
