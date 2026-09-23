"""
EmpleaXChile — Completar perfil del candidato.

Flujo:
  1. Login con cookies guardadas en BQ (o login fresco email/password)
  2. Navegar a /account/profile → subir CV PDF y llenar campos
  3. Guardar cookies actualizadas y marcar cv_completo en BQ

Uso:
    from empleaxchile.completar_perfil import completar_perfil_empleaxchile
    ok = completar_perfil_empleaxchile("jobs2", user_dict)
"""
import os
import sys
import re
import tempfile

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bq

BASE_URL  = "https://empleaxchile.cl"
PORTAL_ID = "empleaxchile"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _download_cv(cv_url: str) -> "str | None":
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
        print(f"  [exc-perfil] CV descargado ({os.path.getsize(path) // 1024} KB)")
        return path
    except Exception as e:
        print(f"  [exc-perfil] CV descarga error: {e}")
        return None


def _get_session(user_id: str, email: str, password: str):
    """Retorna (page, browser, ctx, pw_obj). Todos None si falla."""
    try:
        from playwright.sync_api import sync_playwright
        from portal_accounts import _make_pw_context, _new_stealth_page
    except ImportError as e:
        print(f"  [exc-perfil] Import error: {e}")
        return None, None, None, None

    pw_obj   = sync_playwright().start()
    _, browser, ctx, _ = _make_pw_context(pw_obj)

    cookies = bq.get_portal_cookies(user_id, PORTAL_ID)
    if cookies:
        try:
            ctx.add_cookies(cookies)
        except Exception:
            pass

    page = _new_stealth_page(ctx)

    # Verificar sesión
    try:
        page.goto(f"{BASE_URL}/account", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
        if "login" not in page.url and "external" not in page.url:
            print(f"  [exc-perfil] Sesión restaurada desde cookies")
            return page, browser, ctx, pw_obj
    except Exception:
        pass

    # Login fresco
    print(f"  [exc-perfil] Login fresco: {email}")
    try:
        page.goto(f"{BASE_URL}/external/login", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
        page.fill("input[name='ExternalUserLogin[email]'], input[type='email']", email)
        page.wait_for_timeout(500)

        try:
            btn = page.locator("button:has-text('Continuar'):visible").first
            if btn.count() > 0:
                btn.click()
                page.wait_for_timeout(2000)
        except Exception:
            pass

        try:
            page.fill("input[type='password']:visible", password)
        except Exception:
            pass
        page.wait_for_timeout(300)

        page.locator("button[type='submit']:visible, button:has-text('Ingresar'):visible").first.click()
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(3000)

        if "login" in page.url or "external" in page.url:
            print(f"  [exc-perfil] ! Login falló: {page.url}")
            browser.close()
            pw_obj.stop()
            return None, None, None, None

        print(f"  [exc-perfil] Login OK: {page.url}")
        bq.save_portal_cookies(user_id, PORTAL_ID, ctx.cookies(), email=email, password=password)
        return page, browser, ctx, pw_obj

    except Exception as e:
        print(f"  [exc-perfil] Error login: {e}")
        try:
            browser.close()
        except Exception:
            pass
        pw_obj.stop()
        return None, None, None, None


def _cerrar_ob_overlay(page) -> None:
    """Cierra el overlay de onboarding de EmpleaXChile (#ob-overlay).
    Bloquea aria-modal e intercepta todos los clicks hasta que se cierra."""
    try:
        overlay = page.locator("#ob-overlay")
        if overlay.count() == 0 or not overlay.is_visible(timeout=2000):
            return
        print("  [exc-perfil] ob-overlay detectado — cerrando onboarding")
        # Intentar botones dentro del overlay
        for sel in ["#ob-overlay button.btn-close", "#ob-overlay [aria-label*='lose']",
                    "#ob-overlay button:has-text('Cerrar')", "#ob-overlay button:has-text('Saltar')",
                    "#ob-overlay button:has-text('Finalizar')", "#ob-overlay button:last-of-type"]:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0 and btn.is_visible(timeout=1000):
                    btn.click(force=True)
                    page.wait_for_timeout(800)
                    if not overlay.is_visible(timeout=1000):
                        print("  [exc-perfil] ob-overlay cerrado con botón")
                        return
            except Exception:
                pass
        # Fallback: remover el overlay por JS
        page.evaluate("""() => {
            const el = document.getElementById('ob-overlay');
            if (el) el.remove();
            document.body.classList.remove('ob-open', 'modal-open');
            document.body.style.overflow = '';
        }""")
        page.wait_for_timeout(500)
        print("  [exc-perfil] ob-overlay removido por JS")
    except Exception as e:
        print(f"  [exc-perfil] ! ob-overlay: {e}")


def _aceptar_consentimiento(page):
    """Acepta la pantalla de consentimiento si aparece."""
    if "consentimiento" in page.url:
        print(f"  [exc-perfil] Aceptando consentimiento...")
        for chk in page.locator("input[type='checkbox']:visible").all():
            try:
                if not chk.is_checked():
                    chk.check()
            except Exception:
                pass
        page.wait_for_timeout(400)
        try:
            page.locator("button:has-text('Aceptar y continuar')").first.click()
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            page.wait_for_timeout(2000)
        except Exception:
            pass


# ── Secciones del perfil ──────────────────────────────────────────────────────

def _subir_cv(page, cv_path: str) -> bool:
    """Sube el CV PDF. Busca el input file en la página actual."""
    if not cv_path or not os.path.exists(cv_path):
        return False
    for sel in [
        "input[type='file'][accept*='pdf']",
        "input[type='file'][accept*='.pdf']",
        "input[type='file'][name*='cv']",
        "input[type='file'][name*='curriculum']",
        "input[type='file'][name*='resume']",
        "input[type='file']",
    ]:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                loc.set_input_files(cv_path)
                page.wait_for_timeout(2000)
                print(f"  [exc-perfil] CV subido ({sel})")
                return True
        except Exception:
            pass
    print(f"  [exc-perfil] ! No se encontró input file para CV")
    return False


def _fill_textarea(page, selector: str, value: str, label: str) -> bool:
    if not value:
        return False
    try:
        loc = page.locator(selector).first
        if loc.count() > 0 and loc.is_visible(timeout=3000):
            loc.fill(value)
            print(f"  [exc-perfil] {label}: '{value[:50]}...'")
            return True
    except Exception:
        pass
    return False


def _fill_field(page, selectors: list, value: str, label: str) -> bool:
    if not value:
        return False
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=2000):
                loc.fill(value)
                print(f"  [exc-perfil] {label}: '{value[:50]}'")
                return True
        except Exception:
            pass
    print(f"  [exc-perfil] ! No encontrado: {label}")
    return False


def _click_save(page, label: str = "", container: str = "") -> bool:
    """Busca y clickea un botón de guardar dentro del container especificado."""
    parent = page.locator(container) if container else page
    for sel in [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Guardar')",
        "button:has-text('Actualizar')",
        "button:has-text('Agregar')",
    ]:
        try:
            btn = parent.locator(sel).first
            if btn.count() == 0:
                continue
            txt = ""
            try:
                txt = btn.inner_text()[:40]
            except Exception:
                txt = sel
            # Intento 1: click normal
            try:
                btn.click(timeout=4000)
                print(f"  [exc-perfil] Guardado '{label}': {txt}")
                page.wait_for_timeout(2000)
                return True
            except Exception:
                pass
            # Intento 2: force=True (bypasa pointer-event interception)
            try:
                btn.click(force=True, timeout=3000)
                print(f"  [exc-perfil] Guardado(force) '{label}': {txt}")
                page.wait_for_timeout(2000)
                return True
            except Exception:
                pass
            # Intento 3: JS click (bypasa todo)
            try:
                btn.evaluate("el => el.click()")
                print(f"  [exc-perfil] Guardado(js) '{label}': {txt}")
                page.wait_for_timeout(2000)
                return True
            except Exception:
                pass
        except Exception:
            pass
    # Último recurso: JS scan dentro del container
    scan_sel = (
        f"{container} button[type='submit'], {container} input[type='submit']"
        if container else
        'button[type="submit"], input[type="submit"]'
    )
    result = page.evaluate(f"""() => {{
        for (const btn of document.querySelectorAll({repr(scan_sel)})) {{
            const r = btn.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) {{ btn.click(); return btn.textContent || btn.value || 'ok'; }}
        }}
        return null;
    }}""")
    if result:
        print(f"  [exc-perfil] Guardado(scan) '{label}': {result.strip()[:30]}")
        page.wait_for_timeout(2000)
        return True
    print(f"  [exc-perfil] ! Sin botón guardar para '{label}'")
    return False


# ── Función principal ─────────────────────────────────────────────────────────

def completar_perfil_empleaxchile(user_id: str, user: dict) -> bool:
    cuenta = bq.get_portal_account(user_id, PORTAL_ID)
    if not cuenta:
        print(f"  [exc-perfil] Sin cuenta para {user_id}")
        return False

    email    = cuenta.get("email", "")
    password = cuenta.get("password", "")

    nombre_completo = str(user.get("NOMBRE") or "")
    profesion       = str(user.get("PROFESION") or "")
    resumen         = str(user.get("RESUMEN") or "")
    cv_url          = str(user.get("CV_URL") or "")
    # Campos adicionales para secciones del perfil
    _ = user  # referencia para acceso posterior en las secciones

    cv_path = _download_cv(cv_url)

    page, browser, ctx, pw_obj = _get_session(user_id, email, password)
    if page is None:
        return False

    try:
        _aceptar_consentimiento(page)

        # ── Navegar a /account (página principal del candidato) ─────────────
        page.goto(f"{BASE_URL}/account", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)
        _aceptar_consentimiento(page)

        # Cerrar ob-overlay (onboarding de EmpleaXChile — bloquea aria-modal todos los clicks)
        _cerrar_ob_overlay(page)

        # Cerrar cualquier otro modal/popup
        for close_sel in [
            "button.btn-close:visible", "button:has-text('Cancelar'):visible",
            "[data-bs-dismiss='modal']:visible", ".modal .close:visible",
            "button.swal2-cancel:visible",
        ]:
            try:
                btns = page.locator(close_sel).all()
                for btn in btns:
                    if btn.is_visible(timeout=1000):
                        btn.click()
                        page.wait_for_timeout(500)
                        break
            except Exception:
                pass
        page.wait_for_timeout(1000)

        print(f"  [exc-perfil] Account URL: {page.url}")

        algo_hecho = False

        # ── 1. CV Adjunto ─────────────────────────────────────────────────────
        if cv_path:
            cv_uploaded = False
            try:
                _cerrar_ob_overlay(page)
                btn_cv = page.locator("a[href*='OpenUserPdfForm'], a:has-text('Subir CV'), a:has-text('Actualizar CV')").first
                if btn_cv.count() > 0 and btn_cv.is_visible(timeout=3000):
                    btn_cv.click()
                else:
                    page.evaluate("OpenUserPdfForm()")
                page.wait_for_timeout(2000)

                if _subir_cv(page, cv_path):
                    page.wait_for_timeout(1500)
                    # Botón CV tiene type="button" (no submit) — usar id directo
                    try:
                        page.locator("#uploadCV").click(timeout=5000)
                        print("  [exc-perfil] Guardado 'cv': Guardar currículum")
                        page.wait_for_timeout(2000)
                    except Exception:
                        _click_save(page, "cv")
                    page.wait_for_timeout(3000)

                    # Rechazar IA — llenamos nosotros manualmente
                    try:
                        no_btn = page.locator("button:has-text('No, gracias')").first
                        if no_btn.count() > 0 and no_btn.is_visible(timeout=3000):
                            no_btn.click()
                            print(f"  [exc-perfil] Click 'No, gracias' — llenamos perfil manual")
                            page.wait_for_timeout(2000)
                    except Exception:
                        pass

                    # Volver a /account para que las funciones JS estén disponibles
                    page.goto(f"{BASE_URL}/account", wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(3000)
                    _aceptar_consentimiento(page)
                    # Cerrar modal de preferencias si aparece de nuevo
                    try:
                        close_btn = page.locator("button.btn-close:visible").first
                        if close_btn.count() > 0 and close_btn.is_visible(timeout=2000):
                            close_btn.click()
                            page.wait_for_timeout(500)
                    except Exception:
                        pass

                    algo_hecho = True
                    cv_uploaded = True
            except Exception as e:
                print(f"  [exc-perfil] Error CV: {e}")

            if not cv_uploaded:
                print(f"  [exc-perfil] ! No se pudo subir CV")

        # ── 2. Presentación ───────────────────────────────────────────────────
        if not resumen:
            print("  [exc-perfil] presentación: sin RESUMEN en BQ — saltar")
        if resumen:
            try:
                page.evaluate("OpenEditUserPresentation()")
                page.wait_for_timeout(2000)
                for ta_sel in [
                    "textarea[name*='description']", "textarea[name*='presentation']",
                    "textarea[name*='summary']", "textarea[name*='resumen']",
                    ".modal textarea:visible", "textarea:visible",
                ]:
                    if _fill_textarea(page, ta_sel, resumen, "presentación"):
                        _click_save(page, "presentación", "form:has(textarea[name*='description']), form:has(textarea[name*='presentation']), form:has(textarea[name*='summary']), form:has(textarea[name*='resumen'])")
                        page.wait_for_timeout(1500)
                        algo_hecho = True
                        break
            except Exception as e:
                print(f"  [exc-perfil] Error presentación: {e}")

        # ── 3. Formación Académica — Carrera ──────────────────────────────────
        carrera      = str(user.get("CARRERA") or "")
        institucion  = str(user.get("INSTITUCION") or "")
        anio_inicio  = str(user.get("ANIO_INICIO_ESTUDIOS") or "")
        anio_fin     = str(user.get("ANIO_FIN") or "")
        situacion    = str(user.get("SITUACION_ESTUDIOS") or "")

        if carrera or institucion:
            try:
                # Cerrar cualquier modal abierto antes de abrir el de carrera
                try:
                    page.locator("button.btn-close:visible").first.click()
                    page.wait_for_timeout(500)
                except Exception:
                    pass

                page.evaluate("ShowCareerForm(0,0)")
                page.wait_for_timeout(2000)

                def _select2_fill(pg, select_name: str, value: str, label: str) -> bool:
                    """Abre un Select2 por nombre del <select> subyacente, busca y selecciona."""
                    try:
                        pg.evaluate(f"$('select[name=\"{select_name}\"]').select2('open')")
                        pg.wait_for_timeout(600)
                        search = pg.locator(".select2-search__field:visible").first
                        if search.count() == 0:
                            return False
                        search.fill(value)
                        pg.wait_for_timeout(1500)
                        opt = pg.locator(".select2-results__option:not(.select2-results__option--disabled):visible").first
                        if opt.count() > 0:
                            opt.click()
                            pg.wait_for_timeout(400)
                            print(f"  [exc-perfil] {label}: '{value[:40]}' OK")
                            return True
                        print(f"  [exc-perfil] {label}: sin opciones para '{value[:40]}'")
                        # Cerrar el dropdown
                        pg.evaluate(f"$('select[name=\"{select_name}\"]').select2('close')")
                        return False
                    except Exception as e:
                        print(f"  [exc-perfil] ! {label}: {e}")
                        return False

                # Institución
                _select2_fill(page, "UserCareer[universityName]", institucion, "institución")
                page.wait_for_timeout(300)

                # Carrera
                _select2_fill(page, "UserCareer[careerName]", carrera, "carrera")
                page.wait_for_timeout(300)

                # Carrera afín — buscar opción con "Ingenier" o primera disponible
                try:
                    afin = page.locator("select[name='UserCareer[genericCareer]']")
                    all_opts = [o.inner_text().strip() for o in afin.locator("option").all()]
                    target = next(
                        (o for o in all_opts if "ingenier" in o.lower()), None
                    ) or next((o for o in all_opts[1:] if o), None)
                    if target:
                        afin.select_option(label=target)
                        print(f"  [exc-perfil] carrera afín: '{target}'")
                except Exception as e:
                    print(f"  [exc-perfil] ! carrera afín: {e}")

                # Situación — inferir del campo o del tipo de búsqueda
                try:
                    sit_map = {"estudiand": "Cursando", "egresad": "Egresado",
                               "tituland": "Titulado", "tituled": "Titulado",
                               "cursand":  "Cursando",  "titulo": "Titulado"}
                    sit_val = next((v for k, v in sit_map.items()
                                    if k in situacion.lower()), None)
                    if sit_val is None:
                        # Sin dato: deducir por tipo de búsqueda
                        _tb = str(user.get("TIPO_BUSQUEDA") or "").upper()
                        sit_val = "Cursando" if _tb in ("PRACTICA", "ESTUDIANTE") else "Titulado"
                    page.locator("select[name='UserCareer[state]']").select_option(label=sit_val)
                    print(f"  [exc-perfil] situación: '{sit_val}'")
                except Exception as e:
                    print(f"  [exc-perfil] ! situación: {e}")

                # Mes de inicio — Marzo por defecto
                try:
                    page.locator("select[name='UserCareer[initialMonth]']").select_option(label="Mar")
                    print(f"  [exc-perfil] mes: Mar")
                except Exception as e:
                    print(f"  [exc-perfil] ! mes: {e}")

                # Año de inicio
                if anio_inicio:
                    try:
                        page.locator("select[name='UserCareer[initialYear]']").select_option(label=str(anio_inicio))
                        print(f"  [exc-perfil] año inicio: {anio_inicio}")
                    except Exception as e:
                        print(f"  [exc-perfil] ! año inicio: {e}")

                # Mes y año de término (requeridos cuando es Egresado/Titulado)
                if sit_val in ("Egresado", "Titulado"):
                    # Mes de término — Diciembre por defecto
                    try:
                        page.locator("select[name='UserCareer[endMonth]']").select_option(label="Dic")
                        print("  [exc-perfil] mes fin: Dic")
                    except Exception as e:
                        print(f"  [exc-perfil] ! mes fin: {e}")

                    # Año de término — desde BQ o fallback inicio+5
                    _anio_fin_val = anio_fin
                    if not _anio_fin_val and anio_inicio:
                        try:
                            _anio_fin_val = str(int(anio_inicio) + 5)
                        except Exception:
                            pass
                    if _anio_fin_val:
                        try:
                            page.locator("select[name='UserCareer[endYear]']").select_option(label=_anio_fin_val)
                            print(f"  [exc-perfil] año fin: {_anio_fin_val}")
                        except Exception as e:
                            print(f"  [exc-perfil] ! año fin: {e}")

                _click_save(page, "carrera", "form:has([name^='UserCareer'])")
                page.wait_for_timeout(2000)
                algo_hecho = True
            except Exception as e:
                print(f"  [exc-perfil] Error carrera: {e}")

        # ── 3b. Información General ───────────────────────────────────────────
        try:
            try:
                page.locator("button.btn-close:visible").first.click()
                page.wait_for_timeout(400)
            except Exception:
                pass

            page.evaluate("OpenEditUserInfo()")
            page.wait_for_timeout(2000)

            # Género
            genero_bq = str(user.get("GENERO") or "").strip().lower()
            genero_map = {
                "masculino": "Masculino", "hombre": "Masculino", "m": "Masculino",
                "femenino": "Femenino", "mujer": "Femenino", "f": "Femenino",
                "no binario": "No-binario", "no-binario": "No-binario",
            }
            genero_val = genero_map.get(genero_bq, "Prefiero no responder")
            try:
                gen_sel = page.locator("select[name='userInfo[gender]']")
                if gen_sel.count() > 0:
                    gen_sel.select_option(label=genero_val)
                    print(f"  [exc-perfil] género: '{genero_val}'")
            except Exception as e:
                print(f"  [exc-perfil] ! género: {e}")

            # Región Metropolitana
            def _sel_by_text(pg, selector: str, text: str, label: str) -> bool:
                try:
                    sel = pg.locator(selector).first
                    if sel.count() == 0:
                        return False
                    opts = sel.locator("option").all()
                    match = next((o for o in opts if text.lower() in (o.inner_text() or "").lower()), None)
                    if match:
                        sel.select_option(value=match.get_attribute("value") or "")
                        print(f"  [exc-perfil] {label}: '{match.inner_text().strip()}'")
                        return True
                    return False
                except Exception as e:
                    print(f"  [exc-perfil] ! {label}: {e}")
                    return False

            def _selectize_info_pick(pg, el_id: str, search: str, label: str) -> bool:
                """Selecciona valor en un Selectize por ID del <select> subyacente."""
                # Intento 1: API Selectize.setValue (opciones ya cargadas)
                val = pg.evaluate(f"""() => {{
                    const sel = document.getElementById('{el_id}');
                    if (!sel || !sel.selectize) return null;
                    for (const key in sel.selectize.options) {{
                        const txt = (sel.selectize.options[key].text || '').toLowerCase();
                        if (txt.includes('{search.lower()}')) {{
                            sel.selectize.setValue(key, false);
                            return sel.selectize.options[key].text;
                        }}
                    }}
                    return null;
                }}""")
                if val:
                    print(f"  [exc-perfil] {label}: '{val}'")
                    return True
                # Intento 2: clickear input Selectize, tipear y seleccionar opción
                try:
                    inp = pg.locator(f"#{el_id}-selectized")
                    if inp.count() == 0:
                        return False
                    inp.click()
                    pg.wait_for_timeout(400)
                    inp.type(search, delay=60)
                    pg.wait_for_timeout(2000)
                    opt = pg.locator(".selectize-dropdown .option:visible").filter(
                        has_text=search.split()[0]
                    ).first
                    if opt.count() == 0:
                        opt = pg.locator(".selectize-dropdown .option:visible").first
                    if opt.count() > 0:
                        opt.click()
                        print(f"  [exc-perfil] {label}: '{search}' (type)")
                        return True
                    pg.keyboard.press("Escape")
                except Exception as e:
                    print(f"  [exc-perfil] ! {label}: {e}")
                return False

            region_ok = _selectize_info_pick(page, "regionSelect", "Metropolitana", "región")
            if region_ok:
                page.wait_for_timeout(2500)  # esperar AJAX que carga las comunas

            # Comuna — Ñuñoa (dirección empresa: Rodrigo de Araya 1410)
            comuna_ok = _selectize_info_pick(page, "townshipSelect", "Ñuñoa", "comuna")

            # Dirección — dirección de la empresa
            _fill_field(page, [
                "input[name='userInfo[address]']",
                "input[name='userInfo[direccion]']",
                "input[name*='address']",
            ], "Rodrigo de Araya 1410", "dirección")

            # Teléfonos — usar CELULAR del usuario (strip +56)
            celular_raw = str(user.get("CELULAR") or "").strip()
            if celular_raw.startswith("+56"):
                num = celular_raw[3:]
            elif celular_raw.startswith("56") and len(celular_raw) >= 10:
                num = celular_raw[2:]
            else:
                num = celular_raw
            if num:
                for sel in ["input[name='userInfo[phone]']", "input[name='userInfo[telephone]']",
                            "input[name*='phone']:visible", "input[name*='telefono']:visible"]:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() > 0 and loc.is_visible(timeout=1500):
                            loc.fill(num)
                            print(f"  [exc-perfil] teléfono: '{num}'")
                            break
                    except Exception:
                        pass
                for sel in ["input[name='userInfo[cellphone]']", "input[name='userInfo[mobile]']",
                            "input[name*='cellphone']:visible", "input[name*='celular']:visible"]:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() > 0 and loc.is_visible(timeout=1500):
                            loc.fill(num)
                            print(f"  [exc-perfil] celular: '{num}'")
                            break
                    except Exception:
                        pass

            # Email secundario — email con que el usuario se registró en AplicAI
            email_sec = str(user.get("EMAIL") or "").strip()
            if email_sec:
                _fill_field(page, [
                    "input[name='userInfo[secondaryEmail]']",
                    "input[name='userInfo[secondEmail]']",
                    "input[name*='secondary']:visible",
                    "input[name*='secundario']:visible",
                ], email_sec, "email secundario")

            _click_save(page, "info-general", "form:has([name^='userInfo'])")
            page.wait_for_timeout(1500)
            algo_hecho = True

            # Cerrar modal
            try:
                page.locator("button.btn-close:visible").first.click()
                page.wait_for_timeout(400)
            except Exception:
                pass
        except Exception as e:
            print(f"  [exc-perfil] Error info-general: {e}")

        # ── 4. Preferencias Laborales ─────────────────────────────────────────
        tipo_busqueda = str(user.get("TIPO_BUSQUEDA") or "").upper()
        import re as _re
        pret_raw = str(user.get("pretension_general") or user.get("PRETENSION_GENERAL") or "")
        pret_num = _re.sub(r"[^\d]", "", pret_raw)
        try:
            # Cerrar modal abierto si hay uno
            try:
                page.locator("button.btn-close:visible").first.click()
                page.wait_for_timeout(400)
            except Exception:
                pass

            page.evaluate("OpenAnswerPreferences()")
            page.wait_for_timeout(2000)

            # "Estoy buscando trabajo" — checkbox #jobSeeking
            try:
                chk_buscando = page.locator("#jobSeeking").first
                if chk_buscando.count() > 0 and not chk_buscando.is_checked():
                    chk_buscando.check()
                    print("  [exc-perfil] prefs: buscando trabajo OK")
            except Exception as e:
                print(f"  [exc-perfil] ! prefs buscando: {e}")

            # Tipo de oferta según TIPO_BUSQUEDA
            # jobOfferType_1=Ofertas de empleo  jobOfferType_2=Trabajo estudiantes  jobOfferType_17=Prácticas
            oferta_id_map = {
                "TRABAJO":    "jobOfferType_1",
                "ESTUDIANTE": "jobOfferType_2",
                "PRACTICA":   "jobOfferType_17",
            }
            oferta_id = oferta_id_map.get(tipo_busqueda, "jobOfferType_1")
            try:
                chk_oferta = page.locator(f"#{oferta_id}").first
                if chk_oferta.count() > 0 and not chk_oferta.is_checked():
                    chk_oferta.check()
                    print(f"  [exc-perfil] prefs: tipo oferta #{oferta_id} OK")
            except Exception as e:
                print(f"  [exc-perfil] ! prefs tipo oferta: {e}")

            # Pretensión de renta — solo si es un número
            if pret_num:
                try:
                    sal = page.locator("#salary, input[name='salary']").first
                    if sal.count() > 0:
                        sal.fill(pret_num)
                        print(f"  [exc-perfil] prefs: salary {pret_num}")
                except Exception as e:
                    print(f"  [exc-perfil] ! prefs salary: {e}")

            _click_save(page, "preferencias", "form[action*='answerPreferences']")
            page.wait_for_timeout(1500)
            algo_hecho = True
        except Exception as e:
            print(f"  [exc-perfil] Error preferencias: {e}")

        # ── 5. Idiomas — Español nativo + Inglés si el usuario lo tiene ─────
        idiomas_bq = user.get("IDIOMAS") or user.get("idiomas") or []
        if isinstance(idiomas_bq, str):
            import json as _json
            try:
                idiomas_bq = _json.loads(idiomas_bq)
            except Exception:
                idiomas_bq = [idiomas_bq] if idiomas_bq else []
        # Siempre agregar Español si no está en la lista
        idiomas_a_agregar = []
        nombres = [str(i).lower() for i in idiomas_bq]
        if not any("espa" in n or "spanish" in n for n in nombres):
            idiomas_a_agregar.append(("Español", "Avanzado", "Sí"))
        for idioma in idiomas_bq:
            idioma_str = str(idioma).strip()
            if "español" in idioma_str.lower() or "spanish" in idioma_str.lower():
                idiomas_a_agregar.append((idioma_str, "Avanzado", "Sí"))
            elif "ingles" in idioma_str.lower() or "inglés" in idioma_str.lower() or "english" in idioma_str.lower():
                idiomas_a_agregar.append((idioma_str, "Intermedio", "No"))
            else:
                idiomas_a_agregar.append((idioma_str, "Básico", "No"))

        def _selectize_fill(pg, select_name: str, value: str, label: str) -> bool:
            """Llena un campo Selectize (is-selectize) buscando por texto."""
            try:
                # El input del Selectize tiene id = "SelectName-selectized"
                input_id = select_name.replace("[", "_").replace("]", "") + "-selectized"
                inp = pg.locator(f"#{input_id}").first
                if inp.count() == 0:
                    # Fallback: buscar input dentro de .selectize-control
                    inp = pg.locator(".selectize-control input:visible").first
                if inp.count() == 0:
                    return False
                inp.click()
                pg.wait_for_timeout(400)
                inp.fill(value)
                pg.wait_for_timeout(1800)
                opt = pg.locator(".selectize-dropdown .option:visible").first
                if opt.count() > 0:
                    opt.click()
                    pg.wait_for_timeout(400)
                    print(f"  [exc-perfil] {label}: '{value[:30]}' OK")
                    return True
                print(f"  [exc-perfil] ! {label}: sin opciones para '{value}'")
                return False
            except Exception as e:
                print(f"  [exc-perfil] ! {label}: {e}")
                return False

        for entry in idiomas_a_agregar:
            idioma_nombre = entry[0]
            idioma_nivel  = entry[1] if len(entry) > 1 else "Avanzado"
            idioma_nativo = entry[2] if len(entry) > 2 else "No"
            try:
                # Verificar si el idioma ya existe en la lista del perfil
                ya_existe = page.evaluate(f"""() => {{
                    const sec = document.querySelector('#langs, [id*="lang"], section[aria-label*="dioma"]');
                    if (!sec) return false;
                    return (sec.textContent || '').toLowerCase().includes('{idioma_nombre[:6].lower()}');
                }}""")
                if ya_existe:
                    print(f"  [exc-perfil] idioma '{idioma_nombre}' ya registrado — saltar")
                    algo_hecho = True
                    continue

                try:
                    page.locator("button.btn-close:visible").first.click()
                    page.wait_for_timeout(400)
                except Exception:
                    pass

                page.evaluate("ShowLanguageForm()")
                page.wait_for_timeout(2000)

                # Seleccionar idioma via Selectize API (más confiable que UI)
                lang_val = page.evaluate(f"""() => {{
                    const sel = document.querySelector('select[name="UserLanguage[language]"]');
                    if (!sel) return null;
                    // Intentar via API Selectize
                    if (sel.selectize) {{
                        const opts = sel.selectize.options;
                        for (const key in opts) {{
                            const txt = (opts[key].text || opts[key].label || '').toLowerCase();
                            if (txt.includes('{idioma_nombre.lower()}')) {{
                                sel.selectize.setValue(key, false);
                                return opts[key].text || key;
                            }}
                        }}
                        // Si no hay opciones cargadas aún, buscar en <option>
                    }}
                    for (const o of sel.options) {{
                        if (o.text.toLowerCase().includes('{idioma_nombre.lower()}')) {{
                            sel.value = o.value;
                            sel.dispatchEvent(new Event('change', {{bubbles:true}}));
                            return o.text;
                        }}
                    }}
                    return null;
                }}""")

                if lang_val:
                    print(f"  [exc-perfil] idioma (api): '{lang_val}' OK")
                else:
                    # Fallback: interacción UI Selectize
                    if not _selectize_fill(page, "UserLanguage[language]", idioma_nombre, "idioma"):
                        print(f"  [exc-perfil] ! No se encontró idioma '{idioma_nombre}' en catálogo")
                        try:
                            page.locator("button.btn-close:visible").first.click()
                        except Exception:
                            pass
                        continue
                    # Verificar que el select subyacente tiene valor
                    lang_check = page.evaluate(
                        "document.querySelector('select[name=\"UserLanguage[language]\"]')?.value"
                    )
                    if not lang_check:
                        print(f"  [exc-perfil] ! Selectize no actualizó el select oculto para '{idioma_nombre}'")
                        try:
                            page.locator("button.btn-close:visible").first.click()
                        except Exception:
                            pass
                        continue

                page.wait_for_timeout(300)

                # Nivel y nativo vía JS (evita timeout si Selectize dropdown sigue abierto)
                nivel_ok = page.evaluate(f"""() => {{
                    const sel = document.querySelector('select[name="UserLanguage[level]"]');
                    if (!sel) return false;
                    for (const o of sel.options) {{
                        if (o.text.trim() === '{idioma_nivel}') {{
                            sel.value = o.value;
                            sel.dispatchEvent(new Event('change', {{bubbles:true}}));
                            return true;
                        }}
                    }}
                    if (sel.options.length > 1) {{
                        sel.value = sel.options[sel.options.length-1].value;
                        sel.dispatchEvent(new Event('change', {{bubbles:true}}));
                        return true;
                    }}
                    return false;
                }}""")
                print(f"  [exc-perfil] idioma nivel: '{idioma_nivel}' {'OK' if nivel_ok else 'FALLÓ'}")

                nativo_val = "1" if idioma_nativo == "Sí" else "0"
                page.evaluate(f"""() => {{
                    const sel = document.querySelector('select[name="UserLanguage[native]"]');
                    if (sel) {{ sel.value = '{nativo_val}'; sel.dispatchEvent(new Event('change', {{bubbles:true}})); }}
                }}""")
                print(f"  [exc-perfil] idioma nativo: '{idioma_nativo}'")

                # Guardar: buscar en el formulario que contiene los campos UserLanguage
                save_btn = page.locator(
                    "form:has(select[name='UserLanguage[language]']) button[type='submit'], "
                    "form:has(select[name='UserLanguage[language]']) input[type='submit'], "
                    "form:has(select[name='UserLanguage[language]']) button:has-text('Guardar'), "
                    "form:has(select[name='UserLanguage[language]']) button:has-text('Agregar')"
                ).first
                saved_txt = None
                if save_btn.count() > 0:
                    try:
                        txt_btn = save_btn.inner_text()[:30]
                        btn_id  = save_btn.get_attribute("id") or ""
                        btn_cls = (save_btn.get_attribute("class") or "")[:40]
                        print(f"  [exc-perfil] save_btn: '{txt_btn}' id={btn_id} cls={btn_cls}")
                    except Exception:
                        txt_btn = "guardar"
                    try:
                        save_btn.click(timeout=5000)
                        saved_txt = txt_btn
                        print(f"  [exc-perfil] Guardado idioma '{idioma_nombre}': {txt_btn}")
                    except Exception:
                        try:
                            save_btn.click(force=True, timeout=3000)
                            saved_txt = txt_btn
                            print(f"  [exc-perfil] Guardado(force) idioma '{idioma_nombre}': {txt_btn}")
                        except Exception as ce:
                            print(f"  [exc-perfil] ! No se pudo guardar idioma: {ce}")

                if saved_txt:
                    page.wait_for_timeout(2500)
                    # Verificar que el modal se cerró (éxito)
                    still_open = page.evaluate("!!document.querySelector('.modal.show')")
                    if still_open:
                        print(f"  [exc-perfil] ! Modal idioma sigue abierto — posible error de validación")
                        try:
                            page.locator("button.btn-close:visible").first.click()
                            page.wait_for_timeout(400)
                        except Exception:
                            pass
                    else:
                        algo_hecho = True
                else:
                    print(f"  [exc-perfil] ! Sin botón guardar en modal para '{idioma_nombre}'")
            except Exception as e:
                print(f"  [exc-perfil] Error idioma '{idioma_nombre}': {e}")


        # Actualizar cookies
        bq.save_portal_cookies(user_id, PORTAL_ID, ctx.cookies(), email=email, password=password)

        print(f"  [exc-perfil] {'OK' if algo_hecho else 'Sin cambios'} para {user_id}")
        return True

    except Exception as e:
        import traceback
        print(f"  [exc-perfil] Error: {e}")
        traceback.print_exc()
        return False
    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            pw_obj.stop()
        except Exception:
            pass
