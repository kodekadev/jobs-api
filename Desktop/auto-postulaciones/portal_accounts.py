"""
Creación y gestión de cuentas en portales de empleo.

Flujo:
  1. Verificar si el usuario ya tiene cuenta en el portal (BigQuery CUENTAS_PORTALES)
  2. Si no tiene → crear cuenta con Selenium
  3. Guardar credenciales en BigQuery
  4. Retornar credenciales para usarlas al postular

Portales soportados:
  - trabajando   → https://www.trabajando.cl
"""
import os
import re
import json
import time
import secrets
import string
import tempfile

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

import threading

import bq

# ─── CACHE DE PREGUNTAS/RESPUESTAS ───────────────────────────────────────────
# Guarda automáticamente las respuestas de Claude para reutilizarlas sin costo.
# Formato: { "LABEL_NORMALIZADO|tipo": "respuesta" }

_CACHE_PATH = os.path.join(os.path.dirname(__file__), "preguntas_cache.json")
_cache_lock = threading.Lock()
_qa_cache: "dict | None" = None


def _load_qa_cache() -> dict:
    global _qa_cache
    if _qa_cache is not None:
        return _qa_cache
    with _cache_lock:
        if _qa_cache is not None:
            return _qa_cache
        try:
            with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                _qa_cache = json.load(f)
            print(f"  [cache] {len(_qa_cache)} respuestas cargadas desde {_CACHE_PATH}")
        except FileNotFoundError:
            _qa_cache = {}
        except Exception as e:
            print(f"  [cache] Error leyendo cache: {e}")
            _qa_cache = {}
    return _qa_cache


def _save_qa_cache(cache: dict) -> None:
    with _cache_lock:
        try:
            with open(_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [cache] Error guardando: {e}")


def _cache_key(label: str, inp_type: str) -> str:
    import unicodedata as _uc
    t = (label or "").upper().strip()
    nfkd = _uc.normalize("NFKD", t)
    norm = _uc.normalize("NFKC", nfkd.translate({0x0301: None, 0x0308: None}))
    return f"{norm}|{inp_type.lower()}"


def _get_cached_answer(label: str, inp_type: str) -> "str | None":
    """Retorna respuesta guardada para este label+tipo, o None si no existe."""
    if not label:
        return None
    return _load_qa_cache().get(_cache_key(label, inp_type))


_CV_LABEL_KEYS = {
    "EXPERIENCIA", "TRAYECTORIA", "HISTORIAL", "DESCRIBE", "CUENT", "COMENT",
    "PRESENTACION", "MOTIVACION", "MOTIVA", "POR QUE", "CARTA", "LOGRO",
    "PROYECTO", "RETO", "DESAFIO", "TRABAJO ANTERIOR", "EMPRESA ANTERIOR",
}


def _is_cacheable(q: dict, resp: str) -> bool:
    """
    True si la respuesta es genérica y sirve para cualquier usuario.
    No cachear respuestas basadas en CV/experiencia personal ni datos sensibles.
    """
    label_norm = _cache_key(q.get("label", ""), "").split("|")[0]

    # No cachear preguntas descriptivas / basadas en CV
    if any(k in label_norm for k in _CV_LABEL_KEYS):
        return False

    # No cachear respuestas largas (probablemente personalizadas con el CV)
    if len(resp) > 120:
        return False

    # No cachear si contiene dígitos largos (teléfonos, RUT, renta)
    if re.search(r"\d{6,}", resp):
        return False

    # No cachear si parece un email o nombre propio con apellido
    if re.search(r"@\w+\.\w+", resp):
        return False

    return True


def _save_answers_to_cache(questions: list, answers: dict) -> None:
    """
    Persiste en el cache JSON solo las respuestas genéricas de Claude
    (las que sirven para cualquier usuario, no las basadas en CV personal).
    No sobreescribe respuestas existentes para preservar las mejores versiones.
    """
    _SKIP = {"", "si", "sí", "no", "s", "n", "si.", "sí."}
    cache = _load_qa_cache()
    nuevas = 0
    for i, q in enumerate(questions):
        resp = answers.get(str(i), "").strip()
        if not resp or resp.lower() in _SKIP:
            continue
        if not _is_cacheable(q, resp):
            continue
        key = _cache_key(q.get("label", ""), q.get("type", "text"))
        if key and key not in cache:
            cache[key] = resp
            nuevas += 1
    if nuevas:
        _save_qa_cache(cache)
        print(f"  [cache] +{nuevas} nuevas respuestas genéricas guardadas")


# ─────────────────────────────────────────────────────────────────────────────

_in_cloud_run = bool(os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB"))
HEADLESS = os.environ.get("SELENIUM_HEADLESS", "1" if _in_cloud_run else "0") == "1"
TWOCAPTCHA_KEY = os.environ.get("TWOCAPTCHA_KEY", "")

_xvfb_started = False
_xvfb_lock    = threading.Lock()
_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()

def _ensure_xvfb():
    """Start a virtual display so Chrome can run without --headless in Cloud Run."""
    global _xvfb_started
    with _xvfb_lock:
        if _xvfb_started:
            return
        try:
            import subprocess
            subprocess.Popen(
                ["Xvfb", ":99", "-screen", "0", "1366x768x24", "-ac"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            os.environ["DISPLAY"] = ":99"
            time.sleep(1.5)
            _xvfb_started = True
            print("  [driver] Xvfb :99 iniciado")
        except Exception as e:
            print(f"  [driver] Xvfb falló: {e}")


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _generar_clave() -> str:
    chars = string.ascii_letters + string.digits + "!@#$"
    while True:
        pwd = "".join(secrets.choice(chars) for _ in range(12))
        if (any(c.isupper() for c in pwd) and
                any(c.isdigit() for c in pwd) and
                any(c in "!@#$" for c in pwd)):
            return pwd


def _clean_phone(phone: str) -> str:
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("56") and len(digits) >= 11:
        return digits[2:]
    return digits[-9:] if len(digits) >= 9 else digits


def _solve_recaptcha(sitekey: str, page_url: str, action: str = "login") -> str | None:
    """
    Resuelve reCAPTCHA v3 (con fallback a v2 invisible) usando 2captcha.com.
    Retorna el token o None si TWOCAPTCHA_KEY no está configurado / hay error.
    """
    if not TWOCAPTCHA_KEY:
        print("  [captcha] TWOCAPTCHA_KEY no configurado — omitiendo reCAPTCHA")
        return None
    try:
        # Intentar v3 primero (Trabajando.cl llama grecaptcha.execute(key, {action}) = v3)
        r = requests.post("https://2captcha.com/in.php", data={
            "key": TWOCAPTCHA_KEY,
            "method": "userrecaptcha",
            "googlekey": sitekey,
            "pageurl": page_url,
            "version": "v3",
            "action": action,
            "min_score": "0.3",
            "json": 1,
        }, timeout=30).json()
        if r.get("status") != 1:
            # Fallback a v2 invisible si v3 falla
            print(f"  [captcha] v3 submit error: {r} — intentando v2 invisible")
            r = requests.post("https://2captcha.com/in.php", data={
                "key": TWOCAPTCHA_KEY,
                "method": "userrecaptcha",
                "googlekey": sitekey,
                "pageurl": page_url,
                "invisible": 1,
                "json": 1,
            }, timeout=30).json()
        if r.get("status") != 1:
            print(f"  [captcha] 2captcha submit error: {r}")
            return None
        jid = r["request"]
        print(f"  [captcha] job {jid}, esperando token...")
        for _ in range(18):
            time.sleep(10)
            r2 = requests.get(
                f"https://2captcha.com/res.php?key={TWOCAPTCHA_KEY}&action=get&id={jid}&json=1",
                timeout=30,
            ).json()
            if r2.get("status") == 1:
                print("  [captcha] token OK")
                return r2["request"]
            if r2.get("request") not in ("CAPCHA_NOT_READY", "CAPTCHA_NOT_READY"):
                print(f"  [captcha] error: {r2}")
                return None
        print("  [captcha] timeout (3 min)")
        return None
    except Exception as e:
        print(f"  [captcha] exception: {e}")
        return None


def _make_driver():
    from selenium.webdriver.chrome.service import Service

    chromium_path = "/usr/bin/chromium"
    driver_path   = "/usr/bin/chromedriver"
    in_linux      = os.path.exists(chromium_path)

    # En Cloud Run: usar Xvfb (display virtual) para que Chrome corra sin --headless.
    # Esto elimina TODAS las señales de headless que detectan los portales.
    use_xvfb = in_linux and _in_cloud_run
    if use_xvfb:
        _ensure_xvfb()

    options = Options()
    if HEADLESS and not use_xvfb:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1366,768")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    if in_linux:
        options.binary_location = chromium_path
        service = Service(driver_path)
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)

    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    print(f"  [driver] Chrome listo (xvfb={use_xvfb}, headless={HEADLESS and not use_xvfb})")
    return driver


# ─── TRABAJANDO.CL ────────────────────────────────────────────────────────────

def crear_cuenta_trabajando(nombre: str, apellido: str, celular: str,
                             mail: str, clave: str) -> bool:
    """Crea cuenta en trabajando.cl con Selenium."""
    celular_limpio = _clean_phone(celular) or "912345678"
    driver = None

    try:
        driver = _make_driver()
        wait = WebDriverWait(driver, 20)

        driver.get("https://www.trabajando.cl/crea-tu-curriculum")
        time.sleep(4)

        print(f"  [debug] URL: {driver.current_url}")
        print(f"  [debug] Title: {driver.title}")

        # Esperar el botón de submit
        btn = wait.until(EC.presence_of_element_located(
            (By.XPATH, "//button[contains(text(),'Crear mi cuenta')]")
        ))
        print(f"  [debug] Botón 'Crear mi cuenta' encontrado")

        # Selectores independientes de la estructura del formulario.
        # Vue re-renderiza el DOM al escribir en cada campo, así que NUNCA usamos
        # referencias al árbol del <form> tras el primer llenado.
        SELECTORS = [
            (By.XPATH, "(//form[.//button[contains(text(),'Crear mi cuenta')]]//input)[1]"),
            (By.XPATH, "(//form[.//button[contains(text(),'Crear mi cuenta')]]//input)[2]"),
            (By.CSS_SELECTOR, 'input[placeholder="999999999"]'),
            (By.XPATH, "(//form[.//button[contains(text(),'Crear mi cuenta')]]//input)[4]"),
            (By.CSS_SELECTOR, 'input[type="password"]'),
        ]
        VALUES = [nombre, apellido, celular_limpio, mail, clave]

        def set_field(selector_by, selector_val, value):
            for attempt in range(3):
                try:
                    el = wait.until(EC.presence_of_element_located((selector_by, selector_val)))
                    # Usamos el setter nativo + eventos Vue para no tener stale refs
                    # al escribir caracter por caracter.
                    driver.execute_script("""
                        var el = arguments[0], v = arguments[1];
                        var setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value').set;
                        setter.call(el, v);
                        el.dispatchEvent(new Event('input',  {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                    """, el, value)
                    return
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(0.5)

        print(f"  [debug] Llenando formulario...")
        for i, ((by, sel), val) in enumerate(zip(SELECTORS, VALUES)):
            set_field(by, sel, val)
            time.sleep(0.4)
            print(f"  [debug] Campo {i} llenado, URL: {driver.current_url}")
        time.sleep(1.5)

        print(f"  [debug] Formulario llenado. URL: {driver.current_url}")

        # Cerrar banner de cookies si aparece
        try:
            acepto = driver.find_element(By.XPATH, "//button[contains(text(),'Acepto')]")
            driver.execute_script("arguments[0].click();", acepto)
            time.sleep(0.5)
        except Exception:
            pass

        # Re-encontrar el botón (DOM pudo haber cambiado al llenar el formulario)
        btn = wait.until(EC.presence_of_element_located(
            (By.XPATH, "//button[contains(text(),'Crear mi cuenta')]")
        ))
        driver.execute_script("arguments[0].scrollIntoView(true);", btn)
        time.sleep(0.3)
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(15)

        current_url = driver.current_url
        content = driver.page_source.lower()

        success_signals = ["¡hola ", "hola ", "cuenta-creada", "como-quieres-postular",
                           "mi curriculum", "completar perfil", "dashboard"]
        if any(s in content for s in success_signals) or "crea-tu-curriculum" not in current_url:
            print(f"  -> Registrado OK, URL: {current_url}")
            driver.quit()
            return True

        already_registered = ["ya existe", "correo registrado", "email en uso",
                               "ya está registrado", "already registered"]
        if any(s in content for s in already_registered):
            print(f"  ! Email ya registrado — guardando credencial")
            driver.quit()
            return True

        print(f"  ! Trabajando: formulario no avanzó, URL: {current_url}")
        driver.quit()
        return False

    except Exception as e:
        import traceback
        print(f"  ! Trabajando: error — {e}")
        traceback.print_exc()
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        return False


# ─── CV INTERNO TRABAJANDO.CL ────────────────────────────────────────────────

def _js_set(driver, el, value: str):
    """Setter nativo + eventos Vue para inputs/selects reactivos."""
    driver.execute_script("""
        var el = arguments[0], v = arguments[1];
        var setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
        );
        if (!setter) setter = Object.getOwnPropertyDescriptor(
            window.HTMLTextAreaElement.prototype, 'value'
        );
        if (setter) setter.set.call(el, v);
        el.dispatchEvent(new Event('input',  {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
    """, el, value)


def _autocomplete_pick(driver, inp_el, texto: str, label="") -> bool:
    """
    Escribe texto en un input de autocomplete y selecciona la primera sugerencia.
    Retorna True si se seleccionó algo.
    """
    try:
        inp_el.clear()
    except Exception:
        return False  # Elemento no interactuable
    time.sleep(0.2)
    try:
        inp_el.send_keys(texto)
    except Exception:
        return False
    time.sleep(2)
    sugs = [
        s for s in driver.find_elements(
            By.CSS_SELECTOR,
            "li[role='option'], ul li.cursor-pointer, [class*='suggestion'], "
            "[class*='autocomplete'] li, [class*='dropdown'] li"
        )
        if s.is_displayed() and s.text.strip()
    ]
    if sugs:
        # Prefer exact match (case-insensitive) over first suggestion
        exact = [s for s in sugs if s.text.strip().lower() == texto.lower()]
        chosen = exact[0] if exact else sugs[0]
        print(f"  [cv] autocomplete '{label}': '{chosen.text.strip()[:40]}'")
        driver.execute_script("arguments[0].click();", chosen)
        return True
    inp_el.send_keys(Keys.TAB)
    print(f"  [cv] autocomplete '{label}': sin sugerencias para '{texto}', Tab")
    return False


def _selects_visibles(driver):
    return [s for s in driver.find_elements(By.TAG_NAME, "select") if s.is_displayed()]


def _select_by_value_safe(driver, sel_el, value: str) -> bool:
    target = value
    try:
        opts_vals = [o.get_attribute("value") for o in sel_el.find_elements(By.TAG_NAME, "option")]
        if value not in opts_vals:
            target = next(
                (v for v in opts_vals if v and v not in ("", "Selecciona", "undefined", "null")), value
            )
        # 1. Selenium Select click (triggers native browser selection)
        Select(sel_el).select_by_value(target)
        # 2. Explicit change+input events so Vue v-model picks up the new value
        driver.execute_script("""
            var el = arguments[0];
            el.dispatchEvent(new Event('change', {bubbles:true, composed:true}));
            el.dispatchEvent(new Event('input',  {bubbles:true, composed:true}));
        """, sel_el)
        return True
    except Exception:
        try:
            # Fallback: direct JS value setter
            driver.execute_script(
                "arguments[0].value=arguments[1];"
                "arguments[0].dispatchEvent(new Event('change',{bubbles:true,composed:true}));"
                "arguments[0].dispatchEvent(new Event('input',{bubbles:true,composed:true}));",
                sel_el, target
            )
            return True
        except Exception:
            return False


def _inferir_actividad(user: dict) -> str:
    """Infiere la actividad/rubro de la empresa desde los datos del usuario."""
    texto = " ".join([
        str(user.get("empresa") or ""),
        str(user.get("profesion") or ""),
        str(user.get("resumen") or ""),
        " ".join(json.loads(user.get("cargos") or "[]") if isinstance(user.get("cargos"), str) else (user.get("cargos") or [])),
    ]).lower()
    if any(k in texto for k in ["telecom", "wom", "claro", "entel", "movistar", "postpago"]):
        return "Telecomunicaciones"
    if any(k in texto for k in ["banco", "financier", "segur", "afp", "isapre", "fintech"]):
        return "Banca / Seguros / Finanzas"
    if any(k in texto for k in ["retail", "tienda", "comercio", "venta", "falabella", "ripley"]):
        return "Retail / Comercio"
    if any(k in texto for k in ["salud", "clínica", "hospital", "farmac", "médic"]):
        return "Salud"
    if any(k in texto for k in ["educac", "universid", "colegio", "enseñ"]):
        return "Educación"
    if any(k in texto for k in ["construc", "inmobiliar", "obra", "ingeni"]):
        return "Construcción / Inmobiliaria"
    if any(k in texto for k in ["aliment", "gastrón", "restaur", "agrícol"]):
        return "Alimentos / Bebidas"
    if any(k in texto for k in ["transporte", "logístic", "bodega"]):
        return "Transporte / Logística"
    if any(k in texto for k in ["tecnol", "software", "sistema", "ti ", "it ", "desar", "program"]):
        return "Tecnología"
    return "Servicios"


def _llenar_informacion_personal(driver: webdriver.Chrome, user: dict) -> bool:
    """
    Rellena la sección #/informacion-personal del CV interno de Trabajando.cl.
    Campos: nombre, apellidos, teléfono, cargo/profesión, años de experiencia.
    Solo toca campos vacíos. Siempre intenta Guardar al final.
    """
    try:
        profesion   = str(user.get("profesion") or "").strip()
        experiencia = str(user.get("experiencia") or "").strip()
        nombre      = str(user.get("nombre") or "").strip()
        apellido    = str(user.get("apellido") or "").strip()
        celular     = _clean_phone(str(user.get("celular") or ""))

        driver.get("https://www.trabajando.cl/mi-curriculum#/informacion-personal")
        time.sleep(3)

        # Llenar campos por placeholder
        def _fill_if_empty(keywords: list, value: str, label: str):
            if not value:
                return
            inps = [
                i for i in driver.find_elements(By.XPATH, "//input[@placeholder]")
                if i.is_displayed()
                and any(k in (i.get_attribute("placeholder") or "").lower() for k in keywords)
            ]
            if inps:
                cur = (inps[0].get_attribute("value") or "").strip()
                if not cur:
                    _js_set(driver, inps[0], value)
                    print(f"  [cv] {label}: '{value}'")
                else:
                    print(f"  [cv] {label} ya tiene: '{cur}'")

        _fill_if_empty(["nombre"], nombre, "nombre")
        _fill_if_empty(["apellido"], apellido, "apellido")
        _fill_if_empty(["celular", "teléfono", "telefono", "fono", "móvil"], celular, "telefono")

        # Cargo / profesión — placeholder: 'Escribe tu cargo, profesión u oficio'
        if profesion:
            cargo_inps = [
                i for i in driver.find_elements(By.XPATH, "//input[@placeholder]")
                if i.is_displayed()
                and "cargo" in (i.get_attribute("placeholder") or "").lower()
            ]
            if cargo_inps:
                cur = (cargo_inps[0].get_attribute("value") or "").strip()
                if not cur:
                    _js_set(driver, cargo_inps[0], profesion)
                    print(f"  [cv] cargo/profesion: '{profesion}'")
                else:
                    print(f"  [cv] cargo ya tiene: '{cur}'")

        # Años de experiencia — placeholder: 'N° años'
        if experiencia:
            exp_inps = [
                i for i in driver.find_elements(By.XPATH, "//input[@placeholder]")
                if i.is_displayed()
                and ("año" in (i.get_attribute("placeholder") or "").lower()
                     or "n°" in (i.get_attribute("placeholder") or "").lower()
                     or "años" in (i.get_attribute("placeholder") or "").lower())
            ]
            if exp_inps:
                cur = (exp_inps[0].get_attribute("value") or "").strip()
                if not cur:
                    _js_set(driver, exp_inps[0], experiencia)
                    print(f"  [cv] experiencia: '{experiencia}'")
                else:
                    print(f"  [cv] experiencia ya tiene: '{cur}'")

        time.sleep(0.5)

        # Guardar
        guardar = [
            b for b in driver.find_elements(By.XPATH, "//button[contains(.,'Guardar')]")
            if b.is_displayed()
        ]
        if guardar:
            driver.execute_script("arguments[0].click();", guardar[0])
            time.sleep(2)
            # Verificar errores
            errs = [
                e.text.strip()[:80]
                for e in driver.find_elements(By.CSS_SELECTOR, "[class*='error'],[class*='invalid']")
                if e.is_displayed() and e.text.strip()
            ]
            if errs:
                print(f"  [cv] ! Errores guardar info-personal: {errs}")
                return False
            print("  [cv] informacion-personal guardada OK")
            return True
        else:
            print("  [cv] ! Boton Guardar no encontrado en informacion-personal")
            return False

    except Exception as e:
        print(f"  [cv] Error en informacion-personal: {e}")
        return False


def _esperar_boton_continuar(driver: webdriver.Chrome, paso: int, timeout: int = 60) -> bool:
    """
    Espera hasta que el botón Continuar/Finalizar deje de tener 'btn-disabled'.
    Retorna True si se pudo hacer click, False si timeout.
    """
    import time as _time
    xp_activo = "//button[not(contains(@class,'btn-disabled')) and (contains(.,'Continuar') or contains(.,'Finalizar'))]"
    for i in range(timeout):
        btns = [b for b in driver.find_elements(By.XPATH, xp_activo) if b.is_displayed()]
        if btns:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btns[0])
            driver.execute_script("arguments[0].click();", btns[0])
            print(f"  [cv] Paso {paso}: click '{btns[0].text.strip()}'")
            return True
        _time.sleep(1)
    print(f"  [cv] ! Paso {paso}: timeout esperando botón activo ({timeout}s)")
    return False


def _paso1_datos_personales(driver: webdriver.Chrome, user: dict) -> None:
    """Rellena paso 1: tipo documento + RUT, ciudad, fecha nacimiento, género."""
    # Parsear fecha nacimiento
    fn_raw = str(user.get("fecha_nacimiento") or "").strip()
    fn_dia = fn_mes = fn_ano = ""
    if fn_raw:
        try:
            p = fn_raw.split("-")
            fn_ano, fn_mes, fn_dia = p[0], p[1].zfill(2), p[2].zfill(2)
        except Exception:
            pass

    ubicaciones = user.get("ubicaciones") or []
    if isinstance(ubicaciones, str):
        import json as _json
        try:
            ubicaciones = _json.loads(ubicaciones)
        except Exception:
            ubicaciones = [ubicaciones]
    ciudad = ubicaciones[0] if ubicaciones else "Santiago"
    rut    = str(user.get("rut") or "").strip()

    # ── Tipo documento: seleccionar radio RUN ──────────────────────────────
    radios_run = [r for r in driver.find_elements(
        By.CSS_SELECTOR, "input[type='radio'][value='RUN']"
    ) if r.is_displayed() or True]  # puede estar oculto
    for radio in radios_run:
        if not radio.is_selected():
            driver.execute_script("arguments[0].click();", radio)
            print("  [cv] Radio 'RUN' seleccionado")
            time.sleep(0.5)
        break

    # ── RUT / N° documento ─────────────────────────────────────────────────
    if rut:
        doc_inps = [
            i for i in driver.find_elements(By.XPATH, "//input[@placeholder]")
            if i.is_displayed() and (
                "documento" in (i.get_attribute("placeholder") or "").lower()
                or "rut" in (i.get_attribute("placeholder") or "").lower()
                or "n°" in (i.get_attribute("placeholder") or "").lower()
            )
        ]
        if doc_inps and not (doc_inps[0].get_attribute("value") or "").strip():
            _js_set(driver, doc_inps[0], rut)
            print(f"  [cv] RUT: '{rut}'")
        elif not doc_inps:
            # Buscar el input que tenga maxlength=20 (patrón del HTML dado)
            doc_inps2 = [i for i in driver.find_elements(By.CSS_SELECTOR, "input[maxlength='20']") if i.is_displayed()]
            if doc_inps2 and not (doc_inps2[0].get_attribute("value") or "").strip():
                _js_set(driver, doc_inps2[0], rut)
                print(f"  [cv] RUT (maxlength): '{rut}'")

    # ── Ciudad (autocomplete) ──────────────────────────────────────────────
    autos = [
        i for i in driver.find_elements(By.XPATH, "//input[@placeholder='Escribe y selecciona una opción']")
        if i.is_displayed()
    ]
    for inp in autos:
        if not (inp.get_attribute("value") or "").strip():
            _autocomplete_pick(driver, inp, ciudad, label="Ciudad")
            break

    # ── Selects: fecha nacimiento + género ─────────────────────────────────
    sels = _selects_visibles(driver)
    dia_set = mes_set = ano_set = False
    for sel in sels:
        opts = [o.get_attribute("value") for o in sel.find_elements(By.TAG_NAME, "option")]
        cur  = sel.get_attribute("value") or ""
        if cur:
            continue
        day_vals = [v for v in opts if v.isdigit() and 1 <= int(v) <= 31]
        if "PREFIERO_NO_INFORMAR" in opts or "HOMBRE" in opts:
            _select_by_value_safe(driver, sel, "PREFIERO_NO_INFORMAR")
            print("  [cv] Género: PREFIERO_NO_INFORMAR")
        elif not dia_set and len(day_vals) >= 28:
            val = fn_dia if fn_dia and fn_dia in opts else "15"
            _select_by_value_safe(driver, sel, val)
            print(f"  [cv] Día nacimiento: {val}")
            dia_set = True
        elif not mes_set and "01" in opts and "12" in opts and len(opts) == 13:
            val = fn_mes if fn_mes and fn_mes in opts else "06"
            _select_by_value_safe(driver, sel, val)
            print(f"  [cv] Mes nacimiento: {val}")
            mes_set = True
        elif not ano_set and any(v.isdigit() and 1940 <= int(v) <= 2010 for v in opts if v.isdigit()):
            anos = [v for v in opts if v.isdigit() and 1940 <= int(v) <= 2010]
            objetivo = fn_ano if fn_ano and fn_ano in anos else ("1990" if "1990" in anos else anos[len(anos)//2])
            _select_by_value_safe(driver, sel, objetivo)
            print(f"  [cv] Año nacimiento: {objetivo}")
            ano_set = True


def _paso2_experiencia(driver: webdriver.Chrome, user: dict) -> None:
    """Rellena paso 2: cargo, empresa, jornada, actividad, fechas."""
    import json as _json
    def _parse(val) -> list:
        if not val:
            return []
        return val if isinstance(val, list) else _json.loads(val)

    cargos  = _parse(user.get("cargos"))
    cargo   = cargos[0] if cargos else ""
    empresa = str(user.get("empresa") or "").strip()
    actividad = _inferir_actividad(user)
    anio_inicio = str(user.get("anio_inicio") or "").strip()
    actualmente = bool(user.get("actualmente_trabajando") if user.get("actualmente_trabajando") is not None else True)
    anio_fin    = str(user.get("anio_fin") or "").strip()

    # Cargo
    cargo_inps = [i for i in driver.find_elements(By.XPATH, "//input[@placeholder='Ingresa tu cargo']") if i.is_displayed()]
    if not cargo_inps:
        cargo_inps = [
            i for i in driver.find_elements(By.XPATH, "//input[@placeholder]")
            if i.is_displayed() and "cargo" in (i.get_attribute("placeholder") or "").lower()
        ]
    if cargo_inps and not (cargo_inps[0].get_attribute("value") or "").strip() and cargo:
        _js_set(driver, cargo_inps[0], cargo)
        print(f"  [cv] Cargo: '{cargo}'")

    # Empresa
    if empresa:
        emp_inps = [
            i for i in driver.find_elements(By.XPATH, "//input[@placeholder]")
            if i.is_displayed() and any(k in (i.get_attribute("placeholder") or "").lower()
                for k in ["empresa", "compañ", "organiz", "instituc"])
        ]
        if emp_inps and not (emp_inps[0].get_attribute("value") or "").strip():
            _js_set(driver, emp_inps[0], empresa)
            print(f"  [cv] Empresa: '{empresa}'")

    # Selects: jornada, jerarquía
    sels2 = _selects_visibles(driver)
    for sel in sels2:
        opts = [(o.get_attribute("value"), o.text) for o in sel.find_elements(By.TAG_NAME, "option")]
        cur  = sel.get_attribute("value") or ""
        if cur in ("", "[object Object]", "Selecciona") and len(opts) >= 2:
            if any("object" in str(o[0]).lower() for o in opts):
                try:
                    sel.find_elements(By.TAG_NAME, "option")[1].click()
                    driver.execute_script("arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", sel)
                    print("  [cv] Jornada: opción 1")
                except Exception:
                    pass
            elif any("SUPERVISOR" in str(o[0]) or "POSICION" in str(o[0]) or "GERENCIA" in str(o[0]) for o in opts):
                _select_by_value_safe(driver, sel, "POSICION_SENIOR")
                print("  [cv] Jerarquía: POSICION_SENIOR")

    # Actividad (autocomplete)
    autos2 = [
        i for i in driver.find_elements(By.XPATH, "//input[@placeholder='Escribe y selecciona una opción']")
        if i.is_displayed()
    ]
    for inp in autos2:
        if not (inp.get_attribute("value") or "").strip():
            _autocomplete_pick(driver, inp, actividad, label="Actividad")
            break

    # Checkbox actualmente trabajando
    chks = [c for c in driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']") if c.is_displayed()]
    for chk in chks:
        checked = chk.is_selected()
        if actualmente and not checked:
            driver.execute_script("arguments[0].click();", chk)
            print("  [cv] Actualmente: checked")
        elif not actualmente and checked:
            driver.execute_script("arguments[0].click();", chk)
            print("  [cv] Actualmente: unchecked")
            time.sleep(1)
        break

    # Logros / descripción del cargo — editor contenteditable (Tiptap/ProseMirror)
    resumen = str(user.get("resumen") or "").strip()
    logros  = (
        resumen[:400] if resumen else
        "Gestión y coordinación de equipos, optimización de procesos operacionales "
        "y cumplimiento de objetivos estratégicos de la organización."
    )
    # Buscar el div contenteditable (Tiptap) que esté vacío
    editables = driver.find_elements(By.CSS_SELECTOR, "div[contenteditable='true']")
    for editable in editables:
        if editable.is_displayed():
            current_text = (editable.text or "").strip()
            if not current_text:
                try:
                    driver.execute_script("""
                        var el = arguments[0], text = arguments[1];
                        el.focus();
                        document.execCommand('selectAll', false, null);
                        document.execCommand('insertText', false, text);
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                    """, editable, logros)
                    print(f"  [cv] Logros (contenteditable): '{logros[:60]}...'")
                except Exception as e:
                    print(f"  [cv] ! Logros error: {e}")
            break

    # Fechas inicio (y fin si aplica)
    sels3 = _selects_visibles(driver)
    fecha_inits = 0
    fecha_fins  = 0
    for sel in sels3:
        opts_vals = [o.get_attribute("value") for o in sel.find_elements(By.TAG_NAME, "option")]
        cur = sel.get_attribute("value") or ""
        if cur:
            continue
        is_mes = "01" in opts_vals and "12" in opts_vals and len(opts_vals) == 13
        is_ano = any(v.isdigit() and 2000 <= int(v) <= 2030 for v in opts_vals if v.isdigit())
        if is_mes and fecha_inits == 0 and fecha_fins == 0:
            _select_by_value_safe(driver, sel, "01")
            print("  [cv] Mes inicio: 01")
            fecha_inits += 1
        elif is_ano and fecha_inits == 1 and fecha_fins == 0:
            anos = [v for v in opts_vals if v.isdigit() and 2000 <= int(v) <= 2030]
            objetivo = anio_inicio if anio_inicio and anio_inicio in anos else (anos[-1] if anos else "2023")
            _select_by_value_safe(driver, sel, objetivo)
            print(f"  [cv] Año inicio: {objetivo}")
            fecha_inits += 1
        elif not actualmente and is_mes and fecha_fins == 0 and fecha_inits >= 2:
            _select_by_value_safe(driver, sel, "12")
            print("  [cv] Mes fin: 12")
            fecha_fins += 1
        elif not actualmente and is_ano and fecha_fins == 1:
            anos = [v for v in opts_vals if v.isdigit() and 2000 <= int(v) <= 2030]
            objetivo = anio_fin if anio_fin and anio_fin in anos else (anos[-1] if anos else "2023")
            _select_by_value_safe(driver, sel, objetivo)
            print(f"  [cv] Año fin: {objetivo}")
            fecha_fins += 1


def _paso3_generico(driver: webdriver.Chrome, user: dict) -> None:
    """
    Rellena paso 3 de educacion en Trabajando.cl.
    Campos requeridos:
      1. Nivel de estudios (select)
      2. Institucion (autocomplete — buscar por nombre de universidad)
      3. Carrera (autocomplete — disabled hasta que Institucion tiene valor)
      4. Situacion (select con [object Object])
      5. Anio de termino (select 2026-1957, visible cuando situacion = Titulado/Egresado)
    NO hay campo Pais en este formulario.
    """
    import json as _json
    profesion   = str(user.get("profesion") or "").strip()
    cargos      = user.get("cargos") or []
    if isinstance(cargos, str):
        try:
            cargos = _json.loads(cargos)
        except Exception:
            cargos = [cargos]
    titulo      = profesion or (cargos[0] if cargos else "Administracion de Empresas")
    institucion = str(user.get("institucion") or "Universidad Diego Portales").strip()
    carrera     = str(user.get("carrera") or user.get("profesion") or titulo).strip()
    nivel_key   = str(user.get("nivel_educativo") or "UNIVERSITARIA")
    anio_fin_est = str(user.get("anio_fin_estudios") or "")
    anio_ini_est = str(user.get("anio_inicio_estudios") or "2013")
    if not anio_fin_est:
        anio_fin_est = str(min(int(anio_ini_est) + 5, 2025))

    _NIVELES = {
        "PRIMARIA", "SECUNDARIA", "TECNICO_MEDIO", "TECNICO_PROFESIONAL_SUPERIOR",
        "UNIVERSITARIA", "DIPLOMADO", "POSTGRADO", "MAGISTER", "DOCTORADO", "OTRO"
    }
    if nivel_key not in _NIVELES:
        nivel_key = "UNIVERSITARIA"

    _SITUACIONES = ["Egresado", "Titulado", "Estudiando", "Incompleto"]
    situacion_key = str(user.get("situacion_estudios") or "Titulado").strip()
    if situacion_key not in _SITUACIONES:
        situacion_key = "Titulado"

    def _first_active_input():
        """Primer input visible, no disabled y vacio."""
        for i in driver.find_elements(By.TAG_NAME, "input"):
            if (i.is_displayed()
                    and i.get_attribute("type") not in ("checkbox", "radio", "hidden", "file")
                    and not (i.get_attribute("value") or "").strip()
                    and not i.get_attribute("disabled")):
                return i
        return None

    # ── 1. Nivel de estudios ──────────────────────────────────────────────────
    for sel in _selects_visibles(driver):
        opts_vals = [o.get_attribute("value") for o in sel.find_elements(By.TAG_NAME, "option")]
        if not any(v in _NIVELES for v in opts_vals):
            continue
        cur = sel.get_attribute("value") or ""
        if cur in _NIVELES:
            print(f"  [cv] Paso3 nivel ya: {cur}")
            break
        _select_by_value_safe(driver, sel, nivel_key)
        print(f"  [cv] Paso3 nivel: {nivel_key}")
        time.sleep(2)
        break

    # ── 2. Institucion (primer autocomplete activo tras nivel) ────────────────
    # Terminos de busqueda: palabras mas especificas del nombre (ej "Portales" para Diego Portales)
    _inst_words = [w for w in institucion.split() if len(w) > 4]
    inst_search = _inst_words[-1] if _inst_words else institucion[:10]

    inp = _first_active_input()
    if inp:
        found = _autocomplete_pick(driver, inp, inst_search, label="Paso3-inst")
        if not found:
            # Fallback: sin sugerencias → escribir texto directamente y Tab
            try:
                inp.clear()
                inp.send_keys(institucion)
                inp.send_keys(Keys.TAB)
            except Exception:
                pass
        print(f"  [cv] Paso3 institucion: {institucion} (busqueda: '{inst_search}')")
        time.sleep(2)  # Vue habilita Carrera tras llenar Institucion

    # ── 3. Carrera (autocomplete, disabled hasta que Institucion tiene valor) ─
    carrera_inp = None
    for _ in range(10):
        carrera_inp = _first_active_input()
        if carrera_inp:
            break
        time.sleep(0.5)

    if carrera_inp:
        found = _autocomplete_pick(driver, carrera_inp, carrera, label="Paso3-carrera")
        if not found:
            try:
                carrera_inp.clear()
                carrera_inp.send_keys(carrera)
                carrera_inp.send_keys(Keys.TAB)
            except Exception:
                pass
        print(f"  [cv] Paso3 carrera: {carrera}")
        time.sleep(1)

    # ── 4 y 5. Situacion + Anio de termino (dos pasadas) ─────────────────────
    def _fill_selects():
        year_count = [0]
        for sel in _selects_visibles(driver):
            opts = sel.find_elements(By.TAG_NAME, "option")
            opts_vals = [o.get_attribute("value") for o in opts]
            cur = sel.get_attribute("value") or ""
            if any(v in _NIVELES for v in opts_vals):
                continue
            # Selects de año (rango 1957-2026 segun el HTML)
            if any(v and v.isdigit() and 1957 <= int(v) <= 2026 for v in opts_vals if v):
                if cur and cur.isdigit() and 1957 <= int(cur) <= 2026:
                    year_count[0] += 1
                    continue
                anos = [v for v in opts_vals if v and v.isdigit() and 1957 <= int(v) <= 2026]
                if year_count[0] == 0:
                    objetivo = anio_ini_est if anio_ini_est in anos else anos[len(anos) // 2]
                    _select_by_value_safe(driver, sel, objetivo)
                    print(f"  [cv] Paso3 anio inicio: {objetivo}")
                else:
                    objetivo = anio_fin_est if anio_fin_est in anos else anos[0]
                    _select_by_value_safe(driver, sel, objetivo)
                    print(f"  [cv] Paso3 anio termino: {objetivo}")
                year_count[0] += 1
            # Situacion (valores [object Object])
            elif any("[object" in str(v) for v in opts_vals if v):
                if cur in _SITUACIONES:
                    continue
                opts_txt = [o.text.strip() for o in opts]
                if situacion_key in opts_txt:
                    try:
                        Select(sel).select_by_visible_text(situacion_key)
                        driver.execute_script(
                            "arguments[0].dispatchEvent(new Event('change',{bubbles:true,composed:true}));"
                            "arguments[0].dispatchEvent(new Event('input',{bubbles:true,composed:true}));",
                            sel
                        )
                        print(f"  [cv] Paso3 situacion: {situacion_key}")
                    except Exception as e:
                        print(f"  [cv] Paso3 situacion error: {e}")

    _fill_selects()
    time.sleep(1.5)  # Vue puede mostrar Anio termino despues de seleccionar Situacion
    _fill_selects()  # Segunda pasada para el año de termino


def _llenar_educacion_cv_editor(driver: webdriver.Chrome, user: dict) -> bool:
    """
    Navega a la sección educación del CV editor (post-wizard) y agrega el nivel educativo.
    Prueba varias URLs posibles del router Vue/Nuxt de Trabajando.cl.
    """
    _NIVELES_EDUCATIVOS = {
        "PRIMARIA", "SECUNDARIA", "TECNICO_MEDIO", "TECNICO_PROFESIONAL_SUPERIOR",
        "UNIVERSITARIA", "DIPLOMADO", "POSTGRADO", "MAGISTER", "DOCTORADO", "OTRO"
    }
    import json as _json
    profesion = str(user.get("profesion") or "").strip()
    cargos    = user.get("cargos") or []
    if isinstance(cargos, str):
        try:
            cargos = _json.loads(cargos)
        except Exception:
            cargos = [cargos]
    titulo = profesion or (cargos[0] if cargos else "Administración de Empresas")

    for section_url in [
        "https://www.trabajando.cl/mi-curriculum#/educacion",
        "https://www.trabajando.cl/mi-curriculum#/formacion",
        "https://www.trabajando.cl/mi-curriculum#/estudios",
        "https://www.trabajando.cl/mi-curriculum#/agregar-educacion",
    ]:
        driver.get(section_url)
        time.sleep(3)
        sels = _selects_visibles(driver)
        found = False
        for sel in sels:
            opts_vals = [o.get_attribute("value") for o in sel.find_elements(By.TAG_NAME, "option")]
            if "UNIVERSITARIA" not in opts_vals:
                continue
            found = True
            cur = sel.get_attribute("value") or ""
            if cur in _NIVELES_EDUCATIVOS:
                print(f"  [cv] educación nivel ya tiene: {cur}")
            else:
                _select_by_value_safe(driver, sel, "UNIVERSITARIA")
                print("  [cv] educación nivel: UNIVERSITARIA")
                time.sleep(0.5)
            break

        if not found:
            continue

        # Inputs texto: carrera, institución
        inps = [i for i in driver.find_elements(By.TAG_NAME, "input")
                if i.is_displayed() and i.get_attribute("type") not in ("checkbox", "radio", "hidden", "file")
                and not (i.get_attribute("value") or "").strip()]
        for inp in inps:
            ph  = (inp.get_attribute("placeholder") or "").lower()
            nm  = (inp.get_attribute("name") or inp.get_attribute("id") or "").lower()
            ctx = ph + " " + nm
            if any(k in ctx for k in ["carrera", "título", "titulo", "estudio", "especialidad"]):
                _js_set(driver, inp, titulo)
                print(f"  [cv] educación carrera: '{titulo}'")
            elif any(k in ctx for k in ["institución", "institucion", "universidad", "escuela"]):
                _js_set(driver, inp, "Universidad de Chile")
                print("  [cv] educación institución: 'Universidad de Chile'")

        # Autocomplete de carrera/institución
        autos = [
            i for i in driver.find_elements(By.XPATH, "//input[@placeholder='Escribe y selecciona una opción']")
            if i.is_displayed() and not (i.get_attribute("value") or "").strip()
        ]
        for inp in autos:
            _autocomplete_pick(driver, inp, titulo, label="educación-carrera")
            break

        # Guardar / Agregar
        for lbl in ["Guardar", "Agregar", "Añadir", "Continuar"]:
            btns = [b for b in driver.find_elements(By.XPATH, f"//button[contains(.,'{lbl}')]") if b.is_displayed()]
            if btns:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btns[0])
                driver.execute_script("arguments[0].click();", btns[0])
                print(f"  [cv] educación: click '{lbl}'")
                time.sleep(2)
                break

        return True

    print("  [cv] ! No se encontró sección educación en el editor de CV")
    return False


def completar_cv_trabajando(driver: webdriver.Chrome, user: dict) -> bool:
    """
    Crea el CV estructurado de Trabajando.cl desde cero (3 pasos del wizard).

    Flujo:
      1. Navegar a /mi-curriculum → puede redirigir a cuenta-creada o al dashboard
      2. Si onboarding pendiente: click en 'Con un cv desde cero'
      3. Paso 1 → llenar datos personales → Continuar
      4. Paso 2 → llenar experiencia → Continuar
      5. Paso 3 → llenar formación → Finalizar
      6. Llenar #/informacion-personal (cargo, años de experiencia)
    """
    try:
        # ── 1. Navegar a /mi-curriculum ───────────────────────────────────────
        driver.get("https://www.trabajando.cl/mi-curriculum")
        time.sleep(4)
        url = driver.current_url
        print(f"  [cv] URL inicial: {url}")

        # ── 2. Manejar estado inicial ─────────────────────────────────────────
        if "cuenta-creada" in url or "como-quieres" in url:
            # Onboarding: click en "Con un cv desde cero"
            clicked = False
            for xp in [
                "//div[contains(@class,'tag-manager-crear-cv-desde-cero')]",
                "//div[.//h3[contains(.,'desde cero')]]",
                "//div[.//h3[contains(.,'Crear mi curr')]]",
            ]:
                els = [e for e in driver.find_elements(By.XPATH, xp) if e.is_displayed()]
                if els:
                    driver.execute_script("arguments[0].click();", els[0])
                    print(f"  [cv] Click 'desde cero' ({xp[:50]})")
                    clicked = True
                    time.sleep(3)
                    break
            if not clicked:
                print("  [cv] ! No se encontró card 'desde cero'")
                return False

        elif "archivo-cv" in url:
            # Cuenta en modo archivo: intentar navegar al wizard igualmente
            print("  [cv] Cuenta en modo archivo-cv — intentando wizard desde cero")
            driver.get("https://www.trabajando.cl/mi-curriculum")
            time.sleep(3)

        # Si el CV ya está creado (dashboard normal sin wizard) → solo llenar info-personal
        url2 = driver.current_url
        if ("cuenta-creada" not in url2 and "como-quieres" not in url2
                and "crea-tu-curriculum" not in url2 and "archivo-cv" not in url2):
            # Verificar si hay botón wizard pendiente
            btns_wizard = [b for b in driver.find_elements(
                By.XPATH, "//div[contains(@class,'tag-manager-crear-cv-desde-cero')]"
            ) if b.is_displayed()]
            if not btns_wizard:
                print(f"  [cv] CV ya existente — rellenando info-personal y educación")
                _llenar_educacion_cv_editor(driver, user)
                _llenar_informacion_personal(driver, user)
                return True

        # ── 3. Esperar a que cargue el wizard (paso-1) ────────────────────────
        url3 = driver.current_url
        if "paso-1" not in url3 and "paso-2" not in url3 and "paso-3" not in url3:
            # Wizard aún no está visible — puede que no esté en paso-1 todavía
            # Esperar hasta 10 segundos a que aparezca el botón Continuar
            wizard_cargado = False
            for _ in range(10):
                btns = driver.find_elements(By.XPATH, "//button[contains(.,'Continuar') or contains(.,'Finalizar')]")
                if [b for b in btns if b.is_displayed()]:
                    wizard_cargado = True
                    break
                time.sleep(1)
            if not wizard_cargado:
                print(f"  [cv] ! Wizard no cargó. URL: {driver.current_url}")
                return False

        print(f"  [cv] Wizard cargado. URL: {driver.current_url}")

        # ── PASO 1: Datos personales ──────────────────────────────────────────
        # Verificar que estamos en paso-1 (puede que ya hayamos pasado)
        if "paso-2" not in driver.current_url and "paso-3" not in driver.current_url:
            print("  [cv] === Paso 1: datos personales ===")
            _paso1_datos_personales(driver, user)
            time.sleep(1)
            if not _esperar_boton_continuar(driver, 1, timeout=30):
                return False
            time.sleep(3)

        # ── PASO 2: Experiencia laboral ───────────────────────────────────────
        if "paso-3" not in driver.current_url:
            print("  [cv] === Paso 2: experiencia ===")
            _paso2_experiencia(driver, user)
            time.sleep(1)
            if not _esperar_boton_continuar(driver, 2, timeout=30):
                return False
            time.sleep(3)

        # ── PASO 3: Formación u otro ──────────────────────────────────────────
        print("  [cv] === Paso 3: formación ===")
        _paso3_generico(driver, user)
        time.sleep(1)
        if not _esperar_boton_continuar(driver, 3, timeout=45):
            # Puede que el paso 3 no tenga botón Continuar (ya fue Finalizar)
            print("  [cv] ! Paso 3: no se pudo hacer click en botón — verificando URL")

        time.sleep(3)
        url_final = driver.current_url
        print(f"  [cv] URL tras wizard: {url_final}")

        # ── 4. Educación (por si paso 3 no la guardó) + info-personal ────────
        _llenar_educacion_cv_editor(driver, user)
        _llenar_informacion_personal(driver, user)
        return True

    except Exception as e:
        import traceback
        print(f"  [cv] Error en completar_cv: {e}")
        traceback.print_exc()
        return False


def crear_cv_interno_trabajando(email: str, password: str, user: dict = None) -> bool:
    """
    Punto de entrada público: logea y completa el CV wizard.
    Si se pasa `user`, usa sus datos reales; si no, usa datos mínimos.
    """
    driver = _login_driver_trabajando(email, password)
    if not driver:
        return False
    try:
        return completar_cv_trabajando(driver, user or {})
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# ─── SUBIDA DE CV ─────────────────────────────────────────────────────────────

def upload_cv_trabajando(email: str, password: str, cv_url: str, user: dict = None) -> bool:
    """
    Crea el CV estructurado (CV Trabajando.com) mediante el wizard y luego
    adjunta el PDF del usuario como archivo suplementario.
    Flujo: login → onboarding 'Crear mi currículum' → wizard → adjuntar PDF.
    """
    driver = None
    tmp_path = None

    try:
        # ── Descargar PDF para adjuntar después del wizard ─────────────────────
        if cv_url:
            print(f"  -> Descargando CV desde {cv_url[:60]}...")
            tmp_dir = tempfile.mkdtemp()
            tmp_path = os.path.join(tmp_dir, "CV.pdf")
            if "storage.googleapis.com" in cv_url or cv_url.startswith("gs://"):
                from google.cloud import storage as gcs
                if cv_url.startswith("gs://"):
                    parts = cv_url[5:].split("/", 1)
                else:
                    parts = cv_url.replace("https://storage.googleapis.com/", "").split("/", 1)
                bucket_name, blob_name = parts[0], parts[1]
                gcs_client = gcs.Client()
                blob = gcs_client.bucket(bucket_name).blob(blob_name)
                blob.download_to_filename(tmp_path)
            else:
                r = requests.get(cv_url, timeout=30)
                r.raise_for_status()
                with open(tmp_path, "wb") as f:
                    f.write(r.content)
            print(f"  -> PDF descargado ({os.path.getsize(tmp_path)//1024} KB)")

        # ── Login ──────────────────────────────────────────────────────────────
        driver = _login_driver_trabajando(email, password)
        if not driver:
            print("  ! Login fallido")
            return False
        wait = WebDriverWait(driver, 20)

        # ── Onboarding: el wizard se maneja dentro de completar_cv_trabajando ──
        # (detecta cuenta-creada y hace click en el card correcto)

        # ── Crear CV estructurado via wizard ───────────────────────────────────
        ok = completar_cv_trabajando(driver, user or {})
        print(f"  -> CV Trabajando.com: {'OK' if ok else 'con errores'}")

        # ── Adjuntar PDF como suplemento (opcional) ────────────────────────────
        if tmp_path and os.path.exists(tmp_path):
            try:
                driver.get("https://www.trabajando.cl/mi-curriculum/adjuntar-cv")
                time.sleep(3)
                inp_file = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
                if inp_file:
                    driver.execute_script(
                        "arguments[0].classList.remove('d-none'); arguments[0].style.display='block';",
                        inp_file[0]
                    )
                    inp_file[0].send_keys(tmp_path)
                    time.sleep(3)
                    try:
                        confirmar = wait.until(EC.element_to_be_clickable(
                            (By.XPATH, "//button[contains(text(),'Confirmo')]")
                        ))
                        driver.execute_script("arguments[0].click();", confirmar)
                        time.sleep(3)
                        print("  -> PDF adjunto OK")
                    except Exception:
                        print("  -> PDF: sin modal de confirmación (puede estar OK)")
                else:
                    print("  -> adjuntar-cv: no se encontró input de archivo")
            except Exception as e:
                print(f"  -> PDF adjunto: {e}")

        driver.quit()
        return ok

    except Exception as e:
        import traceback
        print(f"  ! Error configurando CV: {e}")
        traceback.print_exc()
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        return False
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                import shutil
                shutil.rmtree(os.path.dirname(tmp_path), ignore_errors=True)
            except Exception:
                pass


# ─── POSTULACIÓN TRABAJANDO.CL ────────────────────────────────────────────────


def _do_login(driver: webdriver.Chrome, email: str, password: str) -> bool:
    """
    Hace login en el driver dado.
    Retorna True si el login fue exitoso (URL cambió fuera de la página de login).
    """
    import threading
    from selenium.webdriver.common.action_chains import ActionChains

    # Sitekey conocido de Trabajando.cl (fijo, no cambia)
    TBJO_SITEKEY = "6LdSrNIgAAAAAL4UEF1GehSd5-OgS3nypjAmz_hB"
    PAGE_URL     = "https://www.trabajando.cl/ingresa-a-tu-cuenta"

    wait = WebDriverWait(driver, 20)
    try:
        # ── 1a. Bloquear el script real de reCAPTCHA ──────────────────────────
        # Así la librería de Google nunca carga y no puede sobreescribir nuestro mock.
        try:
            driver.execute_cdp_cmd("Network.enable", {})
            driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": [
                "https://www.google.com/recaptcha/api.js*",
                "https://www.google.com/recaptcha/api2/*",
                "https://recaptcha.net/recaptcha/api.js*",
                "https://recaptcha.net/recaptcha/api2/*",
            ]})
            print("  [captcha] recaptcha bloqueado via CDP")
        except Exception as _ce:
            print(f"  [captcha] Network.setBlockedURLs no disponible: {_ce}")

        # ── 1b. Mock completo de grecaptcha + interceptores de red ────────────
        # addScriptToEvaluateOnNewDocument corre ANTES que cualquier JS de la página.
        # Como el script real está bloqueado, nuestro mock es el único grecaptcha.
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                // ── Mock de grecaptcha ─────────────────────────────────────────────
                window.__ourToken = null;
                window.__waiters  = [];
                window.__injectToken = function(token) {
                    window.__ourToken = token;
                    var w = window.__waiters;
                    window.__waiters = [];
                    for (var i = 0; i < w.length; i++) w[i](token);
                };

                var _mockGrecaptcha = {
                    ready: function(cb) { if (typeof cb === 'function') cb(); },
                    execute: function() {
                        return new Promise(function(resolve) {
                            if (window.__ourToken) { resolve(window.__ourToken); }
                            else { window.__waiters.push(resolve); }
                        });
                    },
                    getResponse: function() { return window.__ourToken || ''; },
                    reset: function() {},
                    render: function() { return 0; }
                };

                // Definir con setter que ignora reemplazos para que la lib real no borre el mock
                Object.defineProperty(window, 'grecaptcha', {
                    get: function() { return _mockGrecaptcha; },
                    set: function() {},   // ignora cualquier window.grecaptcha = <lib real>
                    configurable: false,
                    enumerable: true
                });

                // ── Interceptar fetch ──────────────────────────────────────────────
                window.__apiLog = [];
                var _origFetch = window.fetch;
                window.fetch = function() {
                    var url = String(arguments[0]);
                    var opts = arguments[1] || {};
                    var reqBody = '';
                    try {
                        var b = opts.body;
                        if (b) {
                            reqBody = (typeof b === 'string' ? b : JSON.stringify(b)).slice(0, 400);
                        }
                    } catch(e) {}
                    var prom = _origFetch.apply(this, arguments);
                    prom.then(function(r) {
                        r.clone().text().then(function(body) {
                            window.__apiLog.push({url: url.slice(0,120), status: r.status, body: body.slice(0,300), reqBody: reqBody});
                        });
                    }).catch(function(e) {
                        window.__apiLog.push({url: url.slice(0,120), error: String(e), reqBody: reqBody});
                    });
                    return prom;
                };

                // ── Interceptar XHR ────────────────────────────────────────────────
                window.__xhrLog = [];
                var _origXHROpen = XMLHttpRequest.prototype.open;
                var _origXHRSend = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.open = function(method, url) {
                    this.__logUrl = String(url);
                    this.__logMethod = String(method);
                    return _origXHROpen.apply(this, arguments);
                };
                XMLHttpRequest.prototype.send = function(body) {
                    var self = this;
                    var reqBody = '';
                    try {
                        if (body) {
                            reqBody = (typeof body === 'string' ? body : JSON.stringify(body)).slice(0, 400);
                        }
                    } catch(e) {}
                    this.addEventListener('load', function() {
                        var resp = '';
                        try { resp = self.responseText.slice(0,300); } catch(e) {}
                        window.__xhrLog.push({
                            method: self.__logMethod || '',
                            url: (self.__logUrl || '').slice(0,120),
                            status: self.status,
                            body: resp,
                            reqBody: reqBody
                        });
                    });
                    return _origXHRSend.apply(this, arguments);
                };
            """
        })

        # ── 2. Lanzar 2captcha en hilo paralelo mientras carga la página ──────
        token_box = {"token": None}
        def _fetch():
            token_box["token"] = _solve_recaptcha(TBJO_SITEKEY, PAGE_URL, action="login")
        if TWOCAPTCHA_KEY:
            print(f"  [captcha] solicitando token a 2captcha en paralelo...")
            t = threading.Thread(target=_fetch, daemon=True)
            t.start()
        else:
            t = None

        # ── 3. Navegar a la página (el override ya está activo) ───────────────
        driver.get(PAGE_URL)
        time.sleep(3)

        # Cerrar banner de cookies si aparece
        for xp in ["//button[contains(text(),'Acepto')]", "//*[@id='aceptarCookies']"]:
            try:
                driver.find_element(By.XPATH, xp).click()
                time.sleep(0.5)
            except Exception:
                pass

        # ── 4. Llenar email y password (carácter por carácter, estilo humano) ───
        import random as _rnd

        def _human_type(el, text):
            ac = ActionChains(driver)
            for ch in text:
                ac.send_keys_to_element(el, ch).pause(_rnd.uniform(0.06, 0.19))
            ac.perform()

        email_el = wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//form//input[not(@type='password') and not(@type='hidden')]")
        ))
        ActionChains(driver).move_to_element(email_el).pause(_rnd.uniform(0.3, 0.6)).click().perform()
        time.sleep(_rnd.uniform(0.3, 0.6))
        email_el.send_keys(Keys.CONTROL, "a")
        _human_type(email_el, email)
        time.sleep(_rnd.uniform(0.4, 0.8))

        pwd_el = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, 'input[type="password"]')
        ))
        ActionChains(driver).move_to_element(pwd_el).pause(_rnd.uniform(0.3, 0.6)).click().perform()
        time.sleep(_rnd.uniform(0.3, 0.6))
        pwd_el.send_keys(Keys.CONTROL, "a")
        _human_type(pwd_el, password)
        time.sleep(_rnd.uniform(0.6, 1.2))

        email_val = email_el.get_attribute("value") or ""
        pwd_val   = pwd_el.get_attribute("value") or ""
        print(f"  [trabajando] Form email={email_val[:30]!r} pwd_len={len(pwd_val)}")

        # ── 5. Esperar token de 2captcha e inyectarlo ──────────────────────────
        if t:
            t.join(timeout=150)
        token = token_box.get("token")
        if token:
            # 1. Resolver promesas pendientes de grecaptcha.execute() (v3/invisible)
            driver.execute_script("window.__injectToken(arguments[0]);", token)
            # 2. Inyección v2: textarea + invocar callback directamente
            driver.execute_script("""
                var t = arguments[0];
                // Setear campo g-recaptcha-response (v2 usa textarea)
                var el = document.querySelector('textarea[name="g-recaptcha-response"]')
                      || document.querySelector('[name="g-recaptcha-response"]');
                if (el) { el.style.display='block'; el.value = t;
                          el.dispatchEvent(new Event('change',{bubbles:true})); }
                // Invocar callback de botones con data-callback (v2 invisible)
                var btns = document.querySelectorAll('[data-callback]');
                for (var b of btns) {
                    var cb = b.getAttribute('data-callback');
                    if (cb && typeof window[cb]==='function') { window[cb](t); break; }
                }
                // También setear en grecaptcha widget si existe
                if (window.grecaptcha && window.grecaptcha.getResponse) {
                    try { window.__rcResponseOverride = t; } catch(e) {}
                }
            """, token)
            print("  [captcha] token inyectado (v2+v3)")
        elif TWOCAPTCHA_KEY:
            print("  [captcha] sin token — continuando sin CAPTCHA")

        # ── 6. Debug: loggear estructura del formulario y respuesta reCAPTCHA ───
        try:
            form_info = driver.execute_script("""
                var info = {};
                // Botones con data-callback / data-sitekey (reCAPTCHA v2)
                var btns = document.querySelectorAll('[data-callback],[data-sitekey]');
                info.rcButtons = Array.from(btns).map(b => ({
                    tag: b.tagName, text: b.innerText.trim().slice(0,30),
                    callback: b.getAttribute('data-callback'),
                    sitekey: b.getAttribute('data-sitekey')
                }));
                // Campo g-recaptcha-response
                var el = document.querySelector('[name="g-recaptcha-response"]');
                info.rcField = el ? {tag: el.tagName, val_len: el.value.length} : null;
                // window.__ourToken existe?
                info.ourToken = typeof window.__ourToken !== 'undefined' ? window.__ourToken.slice(0,20) : 'undefined';
                // grecaptcha disponible?
                info.hasGrecaptcha = typeof window.grecaptcha !== 'undefined';
                // Funciones globales que podrían ser callbacks
                info.globalFns = Object.keys(window).filter(k =>
                    typeof window[k]==='function' && k.toLowerCase().includes('captcha')
                ).slice(0,5);
                return info;
            """)
            print(f"  [debug] form_info: {form_info}")
        except Exception as _e:
            print(f"  [debug] form_info error: {_e}")

        # ── 7. Click en el botón ───────────────────────────────────────────────
        btn_habilitado = None
        for _ in range(8):
            for xp in [
                "//button[contains(text(),'Entrar') and not(@disabled)]",
                "//button[contains(text(),'Ingresar') and not(@disabled)]",
                "//button[@type='submit' and not(@disabled)]",
                "//form//button[not(@disabled)]",
            ]:
                btns = [b for b in driver.find_elements(By.XPATH, xp) if b.is_displayed()]
                if btns:
                    btn_habilitado = btns[0]
                    break
            if btn_habilitado:
                break
            time.sleep(0.5)

        if btn_habilitado:
            txt = btn_habilitado.text.strip()
            driver.execute_script("arguments[0].scrollIntoView(true);", btn_habilitado)
            time.sleep(_rnd.uniform(0.4, 0.9))
            ActionChains(driver).move_to_element(btn_habilitado).pause(_rnd.uniform(0.3, 0.7)).click().perform()
            print(f"  [trabajando] Click btn habilitado: '{txt}'")
        else:
            pwd_el.send_keys(Keys.RETURN)
            print("  [trabajando] Submit via Keys.RETURN")

        # ── 7. Esperar hasta 20 segundos a que la URL cambie ──────────────────
        for _ in range(20):
            time.sleep(1)
            if "ingresa-a-tu-cuenta" not in driver.current_url:
                break

        # Cerrar cookies post-login
        for xp in ["//button[contains(text(),'Acepto')]", "//*[@id='aceptarCookies']"]:
            try:
                driver.find_element(By.XPATH, xp).click()
            except Exception:
                pass

        # ── 8. Leer logs de red para diagnosticar ─────────────────────────────
        time.sleep(2)  # dar tiempo a que promesas de fetch/XHR se resuelvan
        try:
            api_log = driver.execute_script("return window.__apiLog || [];")
            xhr_log = driver.execute_script("return window.__xhrLog || [];")
            all_net = api_log + xhr_log
            if all_net:
                print(f"  [net] {len(all_net)} peticiones capturadas tras click:")
                for entry in all_net:
                    st  = entry.get("status", "?")
                    url = entry.get("url", "")
                    body = entry.get("body", "")[:200]
                    err = entry.get("error", "")
                    req = entry.get("reqBody", "")[:300]
                    if err:
                        print(f"    [net] ERR  {url} → {err}")
                    else:
                        print(f"    [net] {st}  {url}")
                        if req:
                            print(f"      req: {req}")
                        print(f"      res: {body}")
            else:
                print("  [net] __apiLog + __xhrLog vacíos (ninguna petición capturada)")
        except Exception as _e:
            print(f"  [net] error leyendo logs: {_e}")

        # Verificar mensajes de error en la página
        errores = []
        for sel in [".error", "[class*='error']", "[class*='alert']", ".mensaje-error"]:
            try:
                els = [e for e in driver.find_elements(By.CSS_SELECTOR, sel)
                       if e.is_displayed() and e.text.strip()]
                errores.extend([e.text.strip()[:100] for e in els])
            except Exception:
                pass
        if errores:
            print(f"  [trabajando] Errores en página login: {errores}")

        current = driver.current_url
        success = "ingresa-a-tu-cuenta" not in current
        print(f"  [trabajando] Login {'OK' if success else 'FALLÓ'}: {current}")

        # ── 9. Fallback: login directo vía requests (diagnóstico + fallback) ───
        if not success and token:
            print("  [direct] Intentando login directo con requests...")
            try:
                base_h = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
                    "Origin": "https://www.trabajando.cl",
                    "Referer": "https://www.trabajando.cl/ingresa-a-tu-cuenta",
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/json",
                }
                # Pasar cookies del browser
                sel_cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
                sess = requests.Session()
                sess.cookies.update(sel_cookies)

                # Probar distintos formatos de body (el primero que devuelva 200 gana)
                candidates = [
                    {"email": email, "password": password, "recaptchaToken": token},
                    {"email": email, "password": password, "token": token},
                    {"email": email, "password": password, "recaptcha": token},
                    {"email": email, "password": password, "g-recaptcha-response": token},
                    {"correo": email, "password": password, "recaptchaToken": token},
                    {"email": email, "password": password},   # sin token
                ]
                for body in candidates:
                    r = sess.post(
                        "https://api.trabajando.com/login/login",
                        json=body, headers=base_h, timeout=15,
                    )
                    print(f"  [direct] {list(body.keys())} → {r.status_code}: {r.text[:150]}")
                    if r.status_code in (200, 201):
                        # Inyectar cookies de vuelta en Selenium
                        for name, value in r.cookies.items():
                            try:
                                driver.add_cookie({"name": name, "value": value,
                                                   "domain": ".trabajando.cl"})
                            except Exception:
                                pass
                        driver.get("https://www.trabajando.cl/")
                        time.sleep(3)
                        new_url = driver.current_url
                        success = "ingresa-a-tu-cuenta" not in new_url
                        print(f"  [direct] Login {'OK' if success else 'FALLÓ'}: {new_url}")
                        break
            except Exception as _de:
                print(f"  [direct] error: {_de}")

        return success

    except Exception as e:
        print(f"  [trabajando] Login error: {e}")
        return False


def _login_driver_trabajando(email: str, password: str) -> webdriver.Chrome | None:
    """Crea un driver nuevo, hace login y lo retorna. Retorna None si el login falla."""
    driver = _make_driver()
    driver.get("https://www.trabajando.cl/ingresa-a-tu-cuenta")
    time.sleep(3)
    if _do_login(driver, email, password):
        return driver
    try:
        driver.quit()
    except Exception:
        pass
    return None


def _ensure_logged_in(driver: webdriver.Chrome, email: str, password: str) -> bool:
    """Verifica si la sesión sigue activa; re-loguea si no. Retorna True si logueado."""
    try:
        current = driver.current_url
    except Exception:
        return False
    if "ingresa-a-tu-cuenta" not in current:
        return True
    print("  [trabajando] Sesión expirada — re-logueando...")
    return _do_login(driver, email, password)


def get_trabajando_session(uid: str, email: str, password: str) -> webdriver.Chrome | None:
    """Retorna sesión activa de Trabajando.cl, creándola si no existe."""
    key = f"trabajando_{uid}"

    # 1. Reusar driver en memoria si sigue vivo
    with _sessions_lock:
        if key in _sessions:
            try:
                sess = _sessions[key]
                _ = sess["driver"].current_url
                return sess["driver"]
            except Exception:
                del _sessions[key]

    # 2. Intentar con cookies guardadas en BigQuery (login previo desde PC local)
    try:
        cookies = bq.get_portal_cookies(uid, "trabajando")
        if cookies:
            print(f"  -> Cookies guardadas encontradas para {uid}, inyectando...")
            driver = _make_driver()
            driver.get("https://www.trabajando.cl/")
            time.sleep(2)
            for c in cookies:
                try:
                    cookie = {k: v for k, v in c.items()
                              if k in ("name", "value", "domain", "path", "secure", "httpOnly", "expiry")}
                    driver.add_cookie(cookie)
                except Exception:
                    pass
            driver.get("https://www.trabajando.cl/mi-curriculum")
            time.sleep(3)
            if "ingresa-a-tu-cuenta" not in driver.current_url:
                print(f"  -> Sesion restaurada via cookies OK")
                with _sessions_lock:
                    _sessions[key] = {"driver": driver, "email": email, "password": password}
                return driver
            print(f"  -> Cookies expiradas o invalidas, intentando login normal...")
            try:
                driver.quit()
            except Exception:
                pass
    except Exception as _ce:
        print(f"  -> Error cargando cookies: {_ce}")

    # 3. Fallback: login via formulario (requiere 2captcha)
    driver = _login_driver_trabajando(email, password)
    if driver:
        with _sessions_lock:
            _sessions[key] = {"driver": driver, "email": email, "password": password}
    return driver


def close_trabajando_session(uid: str) -> None:
    """Cierra y elimina la sesión de Trabajando.cl para un usuario."""
    key = f"trabajando_{uid}"
    with _sessions_lock:
        if key in _sessions:
            try:
                _sessions[key]["driver"].quit()
            except Exception:
                pass
            del _sessions[key]
    # También cerrar sesión Playwright si existe
    _close_pw_session(uid)


# ─── PLAYWRIGHT SESSION ────────────────────────────────────────────────────────

_pw_sessions: dict[str, dict] = {}
_pw_lock = threading.Lock()


def _selenium_cookies_to_playwright(cookies: list[dict]) -> list[dict]:
    """Convierte cookies de formato Selenium a formato Playwright."""
    result = []
    for c in cookies:
        pc = {
            "name":  c["name"],
            "value": c["value"],
            "path":  c.get("path", "/"),
        }
        domain = c.get("domain", "")
        if domain:
            pc["domain"] = domain
        else:
            pc["url"] = "https://www.trabajando.cl"
        if c.get("secure"):
            pc["secure"] = True
        if c.get("httpOnly"):
            pc["httpOnly"] = True
        expiry = c.get("expiry") or c.get("expires")
        if expiry and float(expiry) > 0:
            pc["expires"] = float(expiry)
        result.append(pc)
    return result


def get_trabajando_pw_session(uid: str, email: str, password: str):
    """
    Retorna un Playwright Page autenticado para Trabajando.cl.
    Usa cookies de BigQuery. Retorna None si no hay cookies o expiraron.
    """
    from playwright.sync_api import sync_playwright

    key = f"tbj_{uid}"
    with _pw_lock:
        if key in _pw_sessions:
            try:
                page = _pw_sessions[key]["page"]
                _ = page.url  # verifica que sigue vivo
                return page
            except Exception:
                try:
                    _pw_sessions[key]["browser"].close()
                    _pw_sessions[key]["pw"].stop()
                except Exception:
                    pass
                del _pw_sessions[key]

    cookies = bq.get_portal_cookies(uid, "trabajando")
    if not cookies:
        print(f"  -> Sin cookies Playwright para {uid}")
        return None

    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=_in_cloud_run,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
              "--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36"
        ),
        locale="es-CL",
        viewport={"width": 1280, "height": 800},
    )
    try:
        context.add_cookies(_selenium_cookies_to_playwright(cookies))
    except Exception as e:
        print(f"  -> Error inyectando cookies PW: {e}")

    page = context.new_page()
    page.goto("https://www.trabajando.cl/mi-curriculum",
              wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)

    if "ingresa-a-tu-cuenta" in page.url:
        print(f"  -> Cookies PW expiradas para {uid}")
        browser.close()
        pw.stop()
        return None

    print(f"  -> Sesion PW restaurada via cookies OK para {uid}")
    with _pw_lock:
        _pw_sessions[key] = {"pw": pw, "browser": browser, "context": context, "page": page,
                             "email": email, "password": password}
    return page


def _close_pw_session(uid: str) -> None:
    key = f"tbj_{uid}"
    with _pw_lock:
        if key in _pw_sessions:
            try:
                _pw_sessions[key]["browser"].close()
                _pw_sessions[key]["pw"].stop()
            except Exception:
                pass
            del _pw_sessions[key]


def _responder_preguntas_playwright(page, user: dict = {}, job_title: str = "") -> None:
    """Responde preguntas del modal de postulación usando Playwright + Claude + CV."""
    import unicodedata

    def _norm(texto: str) -> str:
        t = (texto or "").upper().strip()
        nfkd = unicodedata.normalize("NFKD", t)
        return unicodedata.normalize("NFKC", nfkd.translate({0x0301: None, 0x0308: None}))

    def _get_label(el) -> str:
        try:
            return page.evaluate("""(el) => {
                function isGeneric(t) {
                    return /^(pregunta|question|respuesta|answer|campo|field)\\s*\\d*$/i.test(t.trim());
                }
                var al = el.getAttribute('aria-label');
                if (al && al.trim().length > 3 && !isGeneric(al)) return al.trim();
                if (el.id) {
                    var lbl = document.querySelector('label[for="' + el.id + '"]');
                    if (lbl && lbl.innerText.trim().length > 3) return lbl.innerText.trim();
                }
                var node = el.parentElement;
                for (var i = 0; i < 10; i++) {
                    if (!node) break;
                    for (var j = 0; j < node.childNodes.length; j++) {
                        var c = node.childNodes[j];
                        var tag = (c.tagName || '').toUpperCase();
                        if (['P','LABEL','SPAN','H3','H4','H5','STRONG','B','LI','DIV'].indexOf(tag) >= 0) {
                            var txt = (c.innerText || c.textContent || '').trim();
                            if (txt.length > 5 && !c.contains(el) && !isGeneric(txt)) return txt;
                        }
                        if (c.nodeType === 3) {
                            var t3 = c.textContent.trim();
                            if (t3.length > 5 && !isGeneric(t3)) return t3;
                        }
                    }
                    node = node.parentElement;
                }
                var ph = el.getAttribute('placeholder');
                if (ph && ph.trim().length > 10 && !isGeneric(ph)) return ph.trim();
                return '';
            }""", el)
        except Exception:
            return ""

    try:
        _EXCL_TYPES = {"hidden", "radio", "checkbox", "submit", "button",
                       "file", "image", "reset", "range", "color"}

        # --- Radio buttons ---
        radios_raw = page.locator("input[type='radio']").all()
        grupos: dict[str, list] = {}
        for r in radios_raw:
            try:
                if not r.is_visible():
                    continue
                name = r.get_attribute("name") or ""
                grupos.setdefault(name, []).append(r)
            except Exception:
                pass

        for name, grupo in grupos.items():
            try:
                elegido = grupo[0]
                preg = _norm(_get_label(grupo[0]))
                for r in grupo:
                    lbl = _norm(_get_label(r) or r.get_attribute("value") or "")
                    if "NO" in preg and "NO" in lbl:
                        elegido = r; break
                    if any(k in preg for k in ["SI", "YES"]) and any(k in lbl for k in ["SI", "YES"]):
                        elegido = r; break
                page.evaluate("(el) => { el.checked = true; el.dispatchEvent(new Event('change',{bubbles:true})); }", elegido)
            except Exception:
                pass

        # --- Inputs y textareas (LLM) ---
        all_inputs = page.locator("input").all()
        visible_inputs = []
        for inp in all_inputs:
            try:
                inp_type = (inp.get_attribute("type") or "text").lower()
                if inp.is_visible() and not inp.get_attribute("disabled") and inp_type not in _EXCL_TYPES:
                    val = inp.input_value() or ""
                    if not val.strip():
                        visible_inputs.append(inp)
            except Exception:
                pass

        all_tas = page.locator("textarea").all()
        visible_tas = []
        for ta in all_tas:
            try:
                if ta.is_visible() and not (ta.input_value() or "").strip():
                    visible_tas.append(ta)
            except Exception:
                pass

        pending = [
            {"el": inp, "kind": "input",
             "label": _get_label(inp),
             "type": (inp.get_attribute("type") or "text").lower(),
             "inputmode": (inp.get_attribute("inputmode") or "").lower(),
             "placeholder": inp.get_attribute("placeholder") or ""}
            for inp in visible_inputs
        ] + [
            {"el": ta, "kind": "textarea",
             "label": _get_label(ta),
             "type": "textarea",
             "inputmode": "",
             "placeholder": ta.get_attribute("placeholder") or ""}
            for ta in visible_tas
        ]

        # ── Paso 1: responder preguntas estándar sin llamar a Claude ─────────────
        answers: dict[int, tuple[str, str]] = {}  # idx → (respuesta, fuente)
        for idx, item in enumerate(pending):
            resp = _standard_answer(item, user, _norm)
            if resp is not None:
                answers[idx] = (resp, "perfil")

        # ── Paso 2: preguntas no resueltas → Claude con CV ────────────────────
        sin_respuesta = [item for idx, item in enumerate(pending) if idx not in answers]
        llm_answers: dict[str, str] = {}
        if sin_respuesta:
            cv_url  = user.get("cv_url") or user.get("CV_URL") or ""
            cv_text = _extract_cv_text(cv_url) if cv_url else ""
            llm_raw = _llm_answer_questions(sin_respuesta, user, cv_text=cv_text, job_title=job_title)
            _save_answers_to_cache(sin_respuesta, llm_raw)
            # llm_raw usa índices 0..N dentro de sin_respuesta → mapear a índices globales
            sin_idx = [idx for idx in range(len(pending)) if idx not in answers]
            for local_i, global_i in enumerate(sin_idx):
                resp = llm_raw.get(str(local_i), "")
                if resp:
                    answers[global_i] = (resp, "Claude")

        _NUMERIC_LABEL_KEYS = {
            "PRETENSION", "SUELDO", "RENTA", "SALARIO", "REMUNERACION", "LIQUID",
            "ANOS DE EXP", "AÑOS DE EXP", "ANOS EXP", "CUANTOS ANOS", "CUANTOS AÑO",
            "ANOS DE EXPERIENCIA", "AÑOS DE EXPERIENCIA",
        }

        def _is_numeric_field(it: dict) -> bool:
            if it.get("type") == "number":
                return True
            if it.get("inputmode") in ("numeric", "decimal"):
                return True
            lbl = _norm((it.get("label") or "") + " " + (it.get("placeholder") or ""))
            return any(k in lbl for k in _NUMERIC_LABEL_KEYS)

        def _clean_numeric(resp: str) -> str:
            digits = re.sub(r"[^\d]", "", resp)
            return digits if digits else resp

        def _fallback_pw(it: dict) -> str:
            """Fallback cuando ni perfil ni Claude respondieron — nunca "Sí" para texto."""
            t = it.get("type", "text")
            if _is_numeric_field(it):
                return re.sub(r"[^\d]", "", str(user.get("pretension_general") or "")) or str(user.get("experiencia") or "5")
            if t == "textarea":
                rv = str(user.get("resumen") or "")
                return rv[:400] if rv else f"Profesional con {str(user.get('experiencia') or '5')} años de experiencia."
            if t in ("tel", "phone"):
                return re.sub(r"\D", "", str(user.get("celular") or ""))[-9:]
            return ""

        # ── Paso 3: rellenar campos ───────────────────────────────────────────
        for idx, item in enumerate(pending):
            try:
                el = item["el"]
                resp, source = answers.get(idx, (_fallback_pw(item), "fallback"))
                if _is_numeric_field(item):
                    resp = _clean_numeric(resp)
                print(f"    [preg/{source}] [{idx}] '{(item['label'] or item['type'])[:40]}' -> '{resp[:40]}'")
                page.evaluate("""([el, val]) => {
                    var proto = el.tagName === 'TEXTAREA'
                        ? window.HTMLTextAreaElement.prototype
                        : window.HTMLInputElement.prototype;
                    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, val);
                    el.dispatchEvent(new Event('input',  {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new FocusEvent('blur', {bubbles: true}));
                }""", [el, resp])
            except Exception:
                pass

        # --- Selects ---
        _SKIP_VALS = {"", "0", "-1", "null", "Selecciona", "Seleccione"}
        for sel_el in page.locator("select").all():
            try:
                if not sel_el.is_visible():
                    continue
                cur = sel_el.input_value() or ""
                if cur and cur not in _SKIP_VALS:
                    continue
                opts = page.evaluate("""(sel) => Array.from(sel.options).map(o => ({value: o.value, text: o.innerText}))""", sel_el)
                elegida = next((o["value"] for o in opts if o["value"] and o["value"] not in _SKIP_VALS), None)
                if elegida:
                    page.evaluate("""([sel, val]) => {
                        sel.value = val;
                        sel.dispatchEvent(new Event('change',{bubbles:true}));
                        sel.dispatchEvent(new Event('input', {bubbles:true}));
                    }""", [sel_el, elegida])
            except Exception:
                pass

        print(f"    [trabajando] Preguntas PW respondidas: {len(grupos)} radios, {len(pending)} inputs")

    except Exception as e:
        print(f"    [trabajando] Error respondiendo preguntas PW: {e}")


def apply_trabajando_playwright(page, job_url: str, user: dict = {}, resumen: str = "", job_title: str = "") -> "dict | bool":
    """
    Postula a un empleo en Trabajando.cl usando Playwright.
    page: Playwright Page ya autenticada con cookies de BigQuery.
    """
    import traceback
    from playwright.sync_api import TimeoutError as PWTimeout

    try:
        page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        if "ingresa-a-tu-cuenta" in page.url:
            print(f"    [trabajando] Redirigido a login")
            return False

        print(f"    [trabajando] Cargado: {page.url[:80]}")

        empresa = descripcion = ""
        for sel in [".empresa", ".nombre-empresa", "[class*='empresa']", ".company"]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=1000):
                    empresa = el.inner_text().strip()[:500]
                    break
            except Exception:
                pass
        for sel in ["[class*='descripcion-oferta']", "[class*='descripcion-empleo']",
                    "[class*='descripcion']", ".job-description"]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=1000):
                    txt = el.inner_text().strip()
                    if len(txt) > 50:
                        descripcion = txt[:5000]
                        break
            except Exception:
                pass

        def _ok():
            return {"ok": True, "empresa": empresa, "descripcion": descripcion}

        # ── Paso 1: click botón postular ──────────────────────────────────────
        clickeado = False
        for sel in [
            '#columnaPostular div button:first-child',
            'button:has-text("Postula")',
            'button:has-text("Postular")',
            'button:has-text("Postúlate")',
        ]:
            try:
                btn = page.locator(sel).first
                btn.wait_for(state="visible", timeout=8000)
                texto = btn.inner_text().strip()
                if any(s in texto.lower() for s in ["ya postulaste", "postulado", "aplicaste"]):
                    print(f"    [trabajando] Ya postulado")
                    return _ok()
                btn.click()
                print(f"    [trabajando] Click en '{texto}'")
                clickeado = True
                break
            except Exception:
                continue

        if not clickeado:
            print(f"    [trabajando] Sin boton postular en {job_url[:70]}")
            return False

        time.sleep(2)

        # ── Paso 2a: Modal CV interno ──────────────────────────────────────────
        try:
            if page.locator("text=solicita un CV Trabajando").is_visible(timeout=2000):
                print(f"    [trabajando] Requiere CV interno — saltando")
                return False
        except Exception:
            pass

        # ── Paso 2b: Modal "postular con tu archivo" ───────────────────────────
        try:
            if page.locator("text=postular con tu archivo").is_visible(timeout=4000):
                print(f"    [trabajando] Modal CV detectado")
                time.sleep(0.8)
                btns = page.locator("button:has-text('Postular')").all()
                if btns:
                    btns[-1].click()
                    print(f"    [trabajando] Click Postular (modal CV)")
                time.sleep(2)
        except Exception:
            pass

        # ── Paso 3: Modal preguntas ────────────────────────────────────────────
        try:
            modal_btns = page.locator("#modalConfirmarPreguntas button").all()
            if modal_btns:
                modal_btns[0].click()
                print(f"    [trabajando] Clickeado: Siguiente (preguntas)")
                time.sleep(2)

                _responder_preguntas_playwright(page, user, job_title=job_title)

                # Esperar que Vue habilite el botón
                for _ in range(12):
                    time.sleep(0.5)
                    enabled = page.locator(
                        "#cabeceraPreguntasEscritorio button:not([disabled])"
                    ).all()
                    if [b for b in enabled if b.is_visible()]:
                        break

                # Click enviar
                for sel in [
                    "#cabeceraPreguntasEscritorio div:nth-child(3) button:not([disabled])",
                    "#cabeceraPreguntasEscritorio button:not([disabled])",
                    "#modalConfirmarPreguntas button:not([disabled]):has-text('Postular')",
                    "#modalConfirmarPreguntas button:not([disabled]):has-text('Enviar')",
                ]:
                    try:
                        btn = page.locator(sel).first
                        if btn.is_visible(timeout=3000):
                            btn.click()
                            time.sleep(2)
                            print(f"    [trabajando] OK Postulado (preguntas)")
                            return _ok()
                    except Exception:
                        continue

                print(f"    [trabajando] Sin boton envio habilitado tras preguntas")
        except Exception:
            pass

        # ── Paso 4: Modal confirmación directa ────────────────────────────────
        try:
            conf_btns = page.locator("#modalConfirmarPostulacion button").all()
            if conf_btns:
                conf_btns[-1].click()
                time.sleep(2)
                print(f"    [trabajando] OK Postulado (confirmacion directa)")
                return _ok()
        except Exception:
            pass

        time.sleep(3)

        # ── Verificar resultado ────────────────────────────────────────────────
        try:
            texto_btn = page.locator("#columnaPostular button").first.inner_text().strip().lower()
            if any(s in texto_btn for s in ["ya postulaste", "postulado", "aplicaste"]):
                print(f"    [trabajando] OK Postulado")
                return _ok()
        except Exception:
            pass

        content = page.content().lower()
        exito = any(s in content for s in [
            "postulación enviada", "te has postulado", "gracias por postular", "postulaste exitosamente"
        ])
        print(f"    [trabajando] {'OK Postulado' if exito else 'Sin confirmar exito'} -> {page.url[:60]}")
        return _ok() if exito else False

    except Exception as e:
        print(f"    [trabajando] Error PW: {e}")
        traceback.print_exc()
        return False


import functools
import unicodedata as _uc


def _norm_label(texto: str) -> str:
    t = (texto or "").upper().strip()
    nfkd = _uc.normalize("NFKD", t)
    return _uc.normalize("NFKC", nfkd.translate({0x0301: None, 0x0308: None}))


def _standard_answer(item: dict, user: dict, norm_fn=None) -> "str | None":
    """
    Responde preguntas del formulario directamente desde el perfil PostulaFacil.
    Cubre variantes en español e inglés de cada campo.
    Retorna None solo si la pregunta no se reconoce (va a Claude).
    """
    norm = norm_fn or _norm_label
    label = norm((item.get("label") or "") + " " + (item.get("placeholder") or ""))
    inp_type = (item.get("type") or "text").lower()

    # ── Datos del perfil ──────────────────────────────────────────────────────
    nombre_completo = str(user.get("NOMBRE") or user.get("nombre") or "")
    partes = nombre_completo.split()

    email_val = str(user.get("EMAIL") or user.get("email") or "")

    cel_raw = str(user.get("celular") or user.get("CELULAR") or user.get("telefono") or user.get("TELEFONO") or "")
    cel_digits = re.sub(r"\D", "", cel_raw)
    cel_9 = cel_digits[-9:] if len(cel_digits) >= 9 else cel_digits

    pret_raw = str(user.get("pretension_general") or user.get("PRETENSION_GENERAL") or "")
    pret_num = re.sub(r"[^\d]", "", pret_raw)

    exp_raw = str(user.get("experiencia") or user.get("EXPERIENCIA") or "5")
    exp_raw = exp_raw.replace(" años", "").replace(" anios", "").replace(" years", "").strip()
    try:
        exp = str(int(exp_raw))
    except ValueError:
        exp = "5"

    profesion_val  = str(user.get("profesion")  or user.get("PROFESION")  or "")
    resumen_val    = str(user.get("resumen")     or user.get("RESUMEN")    or "")
    empresa_val    = str(user.get("empresa")     or user.get("EMPRESA")    or "")
    carrera_val    = str(user.get("carrera")     or user.get("CARRERA")    or "")
    institucion_val = str(user.get("institucion") or user.get("INSTITUCION") or "")
    nivel_ed_val   = str(user.get("nivel_educativo") or user.get("NIVEL_EDUCATIVO") or "")
    situacion_val  = str(user.get("situacion_estudios") or user.get("SITUACION_ESTUDIOS") or "")
    rut_val        = str(user.get("rut") or user.get("RUT") or "")
    fn_val         = str(user.get("fecha_nacimiento") or user.get("FECHA_NACIMIENTO") or "")

    ubicaciones = user.get("ubicaciones") or user.get("UBICACIONES") or []
    if isinstance(ubicaciones, str):
        import json as _j
        try:
            ubicaciones = _j.loads(ubicaciones)
        except Exception:
            ubicaciones = [ubicaciones]
    ciudad_val = (ubicaciones[0] if ubicaciones else "Santiago").split(",")[0].strip()

    desc_exp = resumen_val[:400] if resumen_val else f"Soy {profesion_val} con {exp} años de experiencia en el área."

    # ── 1. Sueldo / pretensión / renta ────────────────────────────────────────
    if any(k in label for k in [
        "PRETENSION", "PRETENSIONES", "EXPECTATIVA DE SUELDO", "EXPECTATIVA SALARIAL",
        "EXPECTATIVA DE RENTA", "EXPECTATIVA ECONOMICA", "EXPECTATIVA DE SALARIO",
        "SUELDO", "SUELDO ESPERADO", "SUELDO PRETENDIDO", "SUELDO LIQUIDO", "SUELDO BRUTO",
        "RENTA", "RENTA ESPERADA", "RENTA PRETENDIDA", "RENTA LIQUIDA", "RENTA BRUTA",
        "SALARIO", "SALARIO ESPERADO", "SALARIO PRETENDIDO", "SALARIO BRUTO", "SALARIO LIQUIDO",
        "REMUNERACION", "REMUNERACION ESPERADA", "REMUNERACION PRETENDIDA",
        "LIQUID", "BRUTO", "COMPENSACION", "COMPENSATION",
        "SALARY", "SALARY EXPECTATION", "EXPECTED SALARY", "DESIRED SALARY",
        "WAGE", "PAY EXPECTATION", "REMUNERATION",
    ]):
        return pret_num or "2000000"

    # ── 2. Años de experiencia (número) ──────────────────────────────────────
    if any(k in label for k in [
        "ANOS DE EXP", "AÑOS DE EXP", "ANOS EXP", "AÑOS EXP",
        "TIEMPO DE EXP", "CUANTOS ANOS", "CUANTOS AÑOS",
        "ANOS TRABAJANDO", "AÑOS TRABAJANDO", "ANOS EN EL AREA", "AÑOS EN EL AREA",
        "ANOS DE TRAYECTORIA", "AÑOS DE TRAYECTORIA",
        "ANOS LABORALES", "AÑOS LABORALES", "ANOS EN EL RUBRO", "AÑOS EN EL RUBRO",
        "YEARS OF EXP", "YEARS EXP", "HOW MANY YEARS", "YEARS EXPERIENCE",
        "YEARS OF EXPERIENCE", "YEARS WORKING",
    ]):
        return exp
    if "EXPERIENCIA" in label and any(k in label for k in [
        "ANOS", "AÑOS", "TIEMPO", "CUANTO", "CUANTOS", "YEARS", "HOW MANY",
    ]):
        return exp

    # ── 3. Teléfono ───────────────────────────────────────────────────────────
    if any(k in label for k in [
        "TELEFONO", "TELÉFONO", "CELULAR", "MOVIL", "MÓVIL", "FONO",
        "NUMERO DE TELEFONO", "NÚMERO DE TELÉFONO", "NUMERO DE CONTACTO",
        "NUMERO CELULAR", "NÚMERO CELULAR", "NUMERO MOVIL", "NÚMERO MÓVIL",
        "CONTACTO TELEFONICO", "CONTACTO TELEFÓNICO",
        "PHONE", "PHONE NUMBER", "CELL", "CELL PHONE", "MOBILE", "MOBILE NUMBER",
        "CONTACT NUMBER", "TELEPHONE",
    ]):
        return cel_9

    # ── 4. Email ──────────────────────────────────────────────────────────────
    if any(k in label for k in [
        "EMAIL", "CORREO", "CORREO ELECTRONICO", "CORREO ELECTRÓNICO",
        "MAIL", "E-MAIL", "DIRECCION DE CORREO", "DIRECCIÓN DE CORREO",
    ]):
        return email_val

    # ── 5. Nombre completo ────────────────────────────────────────────────────
    if any(k in label for k in [
        "NOMBRE COMPLETO", "FULL NAME", "NOMBRE Y APELLIDO", "NOMBRE Y APELLIDOS",
    ]):
        return nombre_completo

    # ── 6. Nombre de pila ────────────────────────────────────────────────────
    if any(k in label for k in [
        "NOMBRE DE PILA", "PRIMER NOMBRE", "FIRST NAME", "GIVEN NAME",
    ]):
        return partes[0] if partes else nombre_completo
    if label.strip() in ("NOMBRE", "NAME", "TU NOMBRE", "YOUR NAME") or label.endswith(" NOMBRE"):
        return partes[0] if partes else nombre_completo

    # ── 7. Apellido ───────────────────────────────────────────────────────────
    if any(k in label for k in [
        "APELLIDO", "APELLIDOS", "LAST NAME", "SURNAME", "FAMILY NAME",
    ]):
        return " ".join(partes[1:]) if len(partes) > 1 else ""

    # ── 8. RUT / Documento de identidad ──────────────────────────────────────
    if any(k in label for k in [
        "RUT", "R.U.T", "RUN", "CEDULA", "CÉDULA", "DNI", "IDENTIFICACION",
        "IDENTIFICACIÓN", "NUMERO DE IDENTIFICACION", "DOCUMENTO DE IDENTIDAD",
        "ID NUMBER", "NATIONAL ID", "TAX ID",
    ]):
        return rut_val

    # ── 9. Fecha de nacimiento ────────────────────────────────────────────────
    if any(k in label for k in [
        "FECHA NAC", "FECHA DE NAC", "NACIMIENTO", "FECHA DE NACIMIENTO",
        "BIRTH", "BIRTH DATE", "DATE OF BIRTH", "BIRTHDATE", "DOB",
        "BORN", "FECHA NACI",
    ]):
        return fn_val

    # ── 10. Profesión / cargo actual ──────────────────────────────────────────
    if any(k in label for k in [
        "PROFESION", "PROFESIÓN", "OCUPACION", "OCUPACIÓN",
        "CARGO ACTUAL", "PUESTO ACTUAL", "TITULO ACTUAL", "TÍTULO ACTUAL",
        "AREA PROFESIONAL", "ÁREA PROFESIONAL", "ESPECIALIDAD",
        "JOB TITLE", "CURRENT JOB", "CURRENT POSITION", "POSITION",
        "ROLE", "OCCUPATION", "PROFESSION",
    ]):
        return profesion_val

    # ── 11. Carrera / título académico ────────────────────────────────────────
    if any(k in label for k in [
        "CARRERA", "TITULO PROFESIONAL", "TÍTULO PROFESIONAL",
        "TITULACION", "TITULACIÓN", "GRADO ACADEMICO", "GRADO ACADÉMICO",
        "DEGREE", "MAJOR", "FIELD OF STUDY", "ACADEMIC DEGREE",
        "TITULO", "TÍTULO",
    ]):
        return carrera_val

    # ── 12. Institución educativa ─────────────────────────────────────────────
    if any(k in label for k in [
        "INSTITUCION", "INSTITUCIÓN", "UNIVERSIDAD", "CASA DE ESTUDIOS",
        "COLEGIO", "INSTITUTO", "ESTABLECIMIENTO EDUCACIONAL",
        "CENTRO DE ESTUDIOS", "PLANTEL", "ESCUELA",
        "UNIVERSITY", "COLLEGE", "SCHOOL", "INSTITUTION", "EDUCATIONAL INSTITUTION",
    ]):
        return institucion_val

    # ── 13. Nivel educativo ───────────────────────────────────────────────────
    if any(k in label for k in [
        "NIVEL EDUCATIVO", "NIVEL DE ESTUDIO", "NIVEL ACADEMICO", "NIVEL ACADÉMICO",
        "GRADO DE INSTRUCCION", "GRADO DE INSTRUCCIÓN", "NIVEL DE FORMACION",
        "NIVEL DE FORMACIÓN", "MAXIMO NIVEL", "MÁXIMO NIVEL",
        "EDUCATION LEVEL", "HIGHEST EDUCATION", "EDUCATIONAL LEVEL",
        "LEVEL OF EDUCATION", "ACADEMIC LEVEL",
    ]):
        return nivel_ed_val

    # ── 14. Situación de estudios ────────────────────────────────────────────
    if any(k in label for k in [
        "SITUACION DE ESTUDIO", "SITUACIÓN DE ESTUDIO",
        "ESTADO DE ESTUDIO", "SITUACION ACADEMICA", "SITUACIÓN ACADÉMICA",
        "ESTADO ACADEMICO", "ESTADO ACADÉMICO",
        "STUDY STATUS", "EDUCATION STATUS", "DEGREE STATUS",
    ]):
        return situacion_val

    # ── 15. Empresa ───────────────────────────────────────────────────────────
    if any(k in label for k in [
        "EMPRESA ACTUAL", "EMPRESA ANTERIOR", "EMPLEADOR ACTUAL", "EMPLEADOR ANTERIOR",
        "DONDE TRABAJAS", "DONDE TRABAJASTE", "LUGAR DE TRABAJO",
        "NOMBRE DE EMPRESA", "NOMBRE DEL EMPLEADOR",
        "CURRENT EMPLOYER", "CURRENT COMPANY", "EMPLOYER", "COMPANY NAME",
        "ORGANIZATION", "WORKPLACE",
    ]):
        return empresa_val

    # ── 16. Ciudad / ubicación ────────────────────────────────────────────────
    if any(k in label for k in [
        "CIUDAD", "CIUDAD DE RESIDENCIA", "CIUDAD ACTUAL", "UBICACION", "UBICACIÓN",
        "LOCALIDAD", "REGION", "REGIÓN", "COMUNA", "DOMICILIO",
        "CIUDAD DONDE VIVES", "LUGAR DE RESIDENCIA",
        "CITY", "LOCATION", "CURRENT LOCATION", "RESIDENCE", "REGION", "AREA",
    ]):
        return ciudad_val

    # ── 17. Disponibilidad ────────────────────────────────────────────────────
    if any(k in label for k in [
        "DISPONIBILIDAD", "DISPONIBILIDAD PARA", "DISPONIBLE PARA", "DISPONIBLE DESDE",
        "CUANDO PUEDES", "CUANDO PODRIAS", "INCORPORACION", "INCORPORACIÓN",
        "INICIO DE ACTIVIDADES", "FECHA DE INICIO", "CUANDO EMPEZARIAS",
        "AVAILABILITY", "AVAILABLE FROM", "START DATE", "WHEN CAN YOU START",
        "NOTICE PERIOD", "JOINING DATE",
    ]):
        return "Inmediata"

    # ── 18. Experiencia descriptiva (abierta) ─────────────────────────────────
    if any(k in label for k in [
        "EXPERIENCIA", "TRAYECTORIA", "HISTORIAL LABORAL", "HISTORIAL PROFESIONAL",
        "BACKGROUND", "WORK EXPERIENCE", "PROFESSIONAL EXPERIENCE",
        "EXPERIENCE", "CAREER BACKGROUND",
    ]):
        return desc_exp

    # ── 19. Presentación / motivación / carta / resumen ───────────────────────
    if any(k in label for k in [
        "PRESENTACION", "PRESENTACIÓN", "MOTIVACION", "MOTIVACIÓN",
        "MOTIVA", "POR QUE POSTULAS", "POR QUÉ POSTULAS",
        "POR QUE TE INTERESA", "POR QUÉ TE INTERESA",
        "CUENT", "COMENT", "CARTA DE PRESENTACION", "CARTA DE PRESENTACIÓN",
        "SOBRE TI", "SOBRE MI", "ACERCA DE TI", "ACERCA DE MI",
        "DESCRIBE", "DESCRI", "RESUMEN PROFESIONAL", "PERFIL PROFESIONAL",
        "HABILIDADES", "FORTALEZAS", "APTITUDES",
        "COVER LETTER", "ABOUT YOU", "ABOUT ME", "SELF DESCRIPTION",
        "TELL US ABOUT", "INTRODUCE YOURSELF", "PERSONAL STATEMENT",
        "WHY DO YOU WANT", "WHY ARE YOU INTERESTED", "MOTIVATION",
    ]):
        return desc_exp

    # ── 20. type=number sin label reconocida ─────────────────────────────────
    if inp_type == "number":
        if pret_num.isdigit() and int(pret_num) > 100_000:
            return pret_num
        return exp

    # ── 21. Textarea sin label reconocida → resumen ───────────────────────────
    if inp_type == "textarea":
        return resumen_val[:400] if resumen_val else f"Profesional con {exp} años de experiencia en el área."

    # ── 22. Cache de respuestas aprendidas ────────────────────────────────────
    cached = _get_cached_answer(item.get("label", ""), inp_type)
    if cached:
        return cached

    # No reconocida → Claude
    return None


@functools.lru_cache(maxsize=16)
def _extract_cv_text(cv_url: str) -> str:
    """Descarga el PDF del CV y extrae su texto. Cacheado por URL."""
    if not cv_url:
        return ""
    try:
        resp = requests.get(cv_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(resp.content))
        pages = [p.extract_text() or "" for p in reader.pages]
        text = "\n".join(pages).strip()
        print(f"    [CV] Extraído: {len(text)} chars, {len(reader.pages)} pág(s)")
        return text[:10000]
    except Exception as e:
        print(f"    [CV] Error extrayendo texto: {e}")
        return ""


def _build_user_profile(user: dict) -> str:
    """
    Construye un perfil completo del candidato con todos los campos disponibles.
    Solo incluye campos que tienen valor — dinámico por usuario.
    """
    def _get(*keys):
        for k in keys:
            v = user.get(k) or user.get(k.upper()) or user.get(k.lower())
            if v not in (None, "", 0, False):
                return str(v)
        return ""

    lines = []

    # Identidad
    nombre = _get("NOMBRE", "nombre")
    if nombre:
        lines.append(f"Nombre completo: {nombre}")

    celular = _get("celular", "CELULAR", "telefono")
    if celular:
        lines.append(f"Teléfono: {celular}")

    rut = _get("rut", "RUT")
    if rut:
        lines.append(f"RUT: {rut}")

    fn = _get("fecha_nacimiento", "FECHA_NACIMIENTO")
    if fn:
        lines.append(f"Fecha de nacimiento: {fn}")

    # Profesión y experiencia
    profesion = _get("profesion", "PROFESION")
    if profesion:
        lines.append(f"Profesión: {profesion}")

    exp = _get("experiencia", "EXPERIENCIA", "PF_EXPERIENCIA")
    exp_num = exp.replace(" años", "").replace(" anios", "").strip() if exp else "5"
    lines.append(f"Años de experiencia: {exp_num}")

    pret_raw = _get("pretension_general", "PRETENSION_GENERAL")
    pret_num = pret_raw.replace(".", "").replace(",", "").replace("$", "").replace(" ", "").strip() if pret_raw else ""
    if pret_num:
        lines.append(f"Pretensión salarial bruta CLP (sin puntos): {pret_num}")

    # Experiencia laboral
    empresa = _get("empresa", "EMPRESA")
    if empresa:
        actualmente = user.get("actualmente_trabajando") or user.get("ACTUALMENTE_TRABAJANDO")
        anio_inicio = _get("anio_inicio", "ANIO_INICIO")
        anio_fin    = _get("anio_fin", "ANIO_FIN")
        estado = "actual" if actualmente else "anterior"
        periodo = ""
        if anio_inicio:
            periodo = f" ({anio_inicio}–{'presente' if actualmente else anio_fin or '?'})"
        lines.append(f"Empresa {estado}: {empresa}{periodo}")

    # Educación
    carrera = _get("carrera", "CARRERA")
    if carrera:
        lines.append(f"Carrera / Título: {carrera}")

    institucion = _get("institucion", "INSTITUCION")
    if institucion:
        lines.append(f"Institución educativa: {institucion}")

    nivel_ed = _get("nivel_educativo", "NIVEL_EDUCATIVO")
    if nivel_ed:
        lines.append(f"Nivel educativo: {nivel_ed}")

    situacion = _get("situacion_estudios", "SITUACION_ESTUDIOS")
    if situacion:
        lines.append(f"Situación de estudios: {situacion}")

    anio_est = _get("anio_inicio_estudios", "ANIO_INICIO_ESTUDIOS")
    if anio_est:
        lines.append(f"Año inicio estudios: {anio_est}")

    # Resumen profesional
    resumen = _get("resumen", "RESUMEN")
    if resumen:
        lines.append(f"\nResumen profesional:\n{resumen}")

    lines.append("\nDisponibilidad: Inmediata")
    return "\n".join(lines)


def _llm_answer_questions(questions: list[dict], user: dict, cv_text: str = "", job_title: str = "") -> dict[str, str]:
    """
    Llama a Claude Haiku con el perfil completo del candidato y las preguntas del formulario.
    Retorna {str(index): respuesta}.
    """
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    if not ANTHROPIC_API_KEY or not questions:
        return {}
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    except ImportError:
        return {}

    perfil_txt = _build_user_profile(user)
    cargo_txt  = f' para el cargo "{job_title}"' if job_title else ""

    # Si hay CV, va primero como contexto principal; el perfil estructurado como complemento
    if cv_text:
        contexto = f"CV DEL CANDIDATO:\n{cv_text}\n\nDATOS ADICIONALES DEL PERFIL:\n{perfil_txt}"
    else:
        contexto = f"PERFIL DEL CANDIDATO:\n{perfil_txt}"

    preguntas_txt = "\n".join(
        f"[{i}] label='{q['label']}' type='{q['type']}' placeholder='{q.get('placeholder', '')}'"
        for i, q in enumerate(questions)
    )

    prompt = f"""Eres esta persona y estás completando un formulario de postulación{cargo_txt} en Chile.

{contexto}

PREGUNTAS DEL FORMULARIO:
{preguntas_txt}

Reglas:
- Responde en primera persona, como si fueras el candidato
- type="number" o preguntas de sueldo/renta/pretensión: solo dígitos sin puntos (ej: "2000000")
- type="tel": número de teléfono (solo 9 dígitos si así lo pide el campo)
- Preguntas abiertas: respuesta concisa (1-3 oraciones) usando experiencias reales del CV/perfil
- Preguntas Sí/No: "Sí" o "No" según corresponda al perfil

Responde SOLO con JSON: {{"0": "respuesta0", "1": "respuesta1", ...}}
Nada más, solo el JSON."""

    try:
        cv_info = f" | CV: {len(cv_text)} chars" if cv_text else " | sin CV"
        print(f"    [Claude] {len(questions)} pregunta(s){cv_info} | cargo: {job_title[:40] or '-'}")
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        data  = json.loads(raw[start:end])
        result = {str(i): str(data.get(str(i), data.get(i, ""))) for i in range(len(questions))}
        print(f"    [Claude] {result}")
        return result
    except Exception as e:
        print(f"    [Claude] ERROR: {e}")
        return {}


def _responder_preguntas(driver, user: dict = {}):
    """
    Responde preguntas del formulario usando lógica tipo NORMALIZADOR:
    normaliza el texto de cada pregunta y elige respuesta apropiada del perfil.
    """
    import unicodedata

    def _norm(texto: str) -> str:
        t = (texto or "").upper().replace("?", "").replace("¿", "").replace("!", "").replace("¡", "").strip()
        nfkd = unicodedata.normalize("NFKD", t)
        return unicodedata.normalize("NFKC", nfkd.translate({0x0301: None, 0x0308: None}))

    def _respuesta(preg_norm: str) -> str:
        nombre_completo = str(user.get("NOMBRE") or user.get("nombre") or "")
        partes   = nombre_completo.split()
        nombre   = partes[0] if partes else ""
        apellido = partes[1] if len(partes) > 1 else ""

        pret_raw = str(user.get("pretension_general") or "")
        pret_num = pret_raw.replace(".", "").replace(",", "").replace("$", "").replace(" ", "").strip()
        pretension = pret_num if pret_num.isdigit() else pret_raw

        exp_raw = str(user.get("experiencia") or "5").replace(" años", "").strip()
        try:
            exp_num = int(exp_raw)
        except Exception:
            exp_num = 5

        profesion = str(user.get("profesion") or "")
        carrera   = str(user.get("carrera") or profesion)
        resumen   = str(user.get("resumen") or "")
        situacion = str(user.get("situacion_estudios") or "Titulado")

        p = preg_norm
        if any(k in p for k in ["PRETENSION", "SUELDO", "RENTA", "SALARIO", "BRUTO", "LIQUID", "SALARIAL", "EXPECTATIVA"]):
            return pretension or "2000000"
        if any(k in p for k in ["CUANTOS ANOS DE EXPERIENCIA", "ANOS DE EXPERIENCIA", "ANOS DE EXP"]):
            return str(exp_num)
        if "LIDERAZGO" in p:
            return str(max(exp_num - 1, 1))
        if any(k in p for k in ["CUANTOS ANOS", "CUANTOS ANO", "HOW MANY", "ANOS"]):
            return str(exp_num)
        if any(k in p for k in ["TELEFONO", "CELULAR", "PHONE", "NUMERO DE CONTACTO", "INDICA NUMER"]):
            cel = str(user.get("celular") or user.get("telefono") or "")
            return cel or "+56912345678"
        if "DISPONIBILIDAD" in p:
            return "Inmediata"
        if "SEMANAS" in p:
            return "1"
        if any(k in p for k in ["CIUDAD", "CITY", "UBICACION"]):
            ubs = user.get("ubicaciones") or "Santiago"
            return (ubs[0] if isinstance(ubs, list) else str(ubs).split(",")[0]).strip()
        if any(k in p for k in ["LICENCIA", "CONDUCIR", "MANEJO"]):
            return "No"
        if "COMENTE CARRERA" in p or ("CARRERA" in p and "ESTADO" in p):
            return f"{situacion}, {carrera}"
        if any(k in p for k in ["FORMACION ACADEMICA", "TITULO PROFESIONAL"]):
            return carrera
        if any(k in p for k in ["CARTA DE PRESENTACION", "PRESENTACION", "COVER"]):
            return resumen or f"Soy {profesion} con {exp_num} años de experiencia."
        if "COMENTE" in p and "EXPERIENCIA" in p:
            return (f"Soy {profesion} con {exp_num} años de experiencia. {resumen}")[:300]
        if "FIRST NAME" in p:
            return nombre
        if "LAST NAME" in p or "APELLIDO" in p:
            return apellido
        if any(k in p for k in ["DISCAPACIDAD", "AJUSTE", "REQUIERES"]):
            return "No"
        if "NIVEL" in p:
            return "Avanzado"
        return "Sí"

    def _get_label(el) -> str:
        """JS: extrae el texto de pregunta más cercano al elemento del formulario."""
        try:
            return driver.execute_script("""
                var el = arguments[0];
                function isGeneric(t) {
                    return /^(pregunta|question|respuesta|answer|campo|field)\\s*\\d*$/i.test(t.trim());
                }
                var al = el.getAttribute('aria-label');
                if (al && al.trim().length > 3 && !isGeneric(al)) return al.trim();
                if (el.id) {
                    var lbl = document.querySelector('label[for="' + el.id + '"]');
                    if (lbl && lbl.innerText && lbl.innerText.trim().length > 3) return lbl.innerText.trim();
                }
                var node = el.parentElement;
                for (var i = 0; i < 10; i++) {
                    if (!node) break;
                    var children = node.childNodes;
                    for (var j = 0; j < children.length; j++) {
                        var c = children[j];
                        var tag = (c.tagName || '').toUpperCase();
                        if (['P','LABEL','SPAN','H3','H4','H5','STRONG','B','LI','DIV'].indexOf(tag) >= 0) {
                            var txt = (c.innerText || c.textContent || '').trim();
                            if (txt.length > 5 && !c.contains(el) && !isGeneric(txt)) return txt;
                        }
                        if (c.nodeType === 3) {
                            var t3 = c.textContent.trim();
                            if (t3.length > 5 && !isGeneric(t3)) return t3;
                        }
                    }
                    node = node.parentElement;
                }
                var ph = el.getAttribute('placeholder');
                if (ph && ph.trim().length > 10 && !isGeneric(ph)) return ph.trim();
                return '';
            """, el)
        except Exception:
            return ""

    try:
        # --- Radios: agrupar por name, detectar pregunta, elegir opción apropiada ---
        radios = [r for r in driver.find_elements(By.CSS_SELECTOR, "input[type='radio']") if r.is_displayed()]
        grupos: dict = {}
        for radio in radios:
            name = radio.get_attribute("name") or radio.get_attribute("id") or id(radio)
            grupos.setdefault(name, []).append(radio)

        for name, grupo in grupos.items():
            try:
                preg = _norm(_get_label(grupo[0]))
                resp_norm = _norm(_respuesta(preg))
                elegido = None

                # Si la respuesta es "No", buscar opción "No"
                if resp_norm == "NO":
                    for r in grupo:
                        try:
                            r_lbl = _norm(_get_label(r) or r.get_attribute("value") or "")
                            if "NO" in r_lbl:
                                elegido = r; break
                        except Exception:
                            pass
                else:
                    # Para cualquier otra respuesta (incluido "Sí" por defecto),
                    # buscar opción afirmativa: Sí, Si, Yes, True, 1
                    for r in grupo:
                        try:
                            r_lbl = _norm(_get_label(r) or r.get_attribute("value") or "")
                            if any(k in r_lbl for k in ("SI", "YES", "SÍ", "TRUE", "VERDADERO")):
                                elegido = r; break
                        except Exception:
                            pass

                if not elegido:
                    elegido = grupo[0]

                driver.execute_script("""
                    arguments[0].checked = true;
                    arguments[0].dispatchEvent(new Event('change', {bubbles: true}));
                    arguments[0].dispatchEvent(new Event('input',  {bubbles: true}));
                """, elegido)
                print(f"    [radio] '{preg[:40]}' -> '{(_get_label(elegido) or elegido.get_attribute('value') or '')[:20]}'")
            except Exception:
                pass

        # --- Text / number / email / tel inputs (todos los tipos relevantes) ---
        _EXCL_TYPES = {"hidden", "radio", "checkbox", "submit", "button", "file", "image", "reset", "range", "color"}
        inputs = [
            i for i in driver.find_elements(By.TAG_NAME, "input")
            if i.is_displayed()
            and not i.get_attribute("disabled")
            and (i.get_attribute("type") or "text").lower() not in _EXCL_TYPES
        ]
        # Recopilar textareas vacíos también (para llamada LLM conjunta)
        textareas = [
            ta for ta in driver.find_elements(By.TAG_NAME, "textarea")
            if ta.is_displayed() and not (ta.get_attribute("value") or "").strip()
        ]

        # Construir lista de preguntas para Claude (inputs + textareas vacíos)
        pending_inputs = [
            inp for inp in inputs
            if not (inp.get_attribute("value") or "").strip()
        ]
        pending_all = [
            {"el": inp, "kind": "input",
             "label": _get_label(inp),
             "type": (inp.get_attribute("type") or "text").lower(),
             "inputmode": (inp.get_attribute("inputmode") or "").lower(),
             "placeholder": inp.get_attribute("placeholder") or ""}
            for inp in pending_inputs
        ] + [
            {"el": ta, "kind": "textarea",
             "label": _get_label(ta),
             "type": "textarea",
             "inputmode": "",
             "placeholder": ta.get_attribute("placeholder") or ""}
            for ta in textareas
        ]

        # ── Paso 1: responder preguntas estándar desde el perfil ─────────────────
        answers_sel: dict[int, tuple[str, str]] = {}
        for idx, item in enumerate(pending_all):
            resp = _standard_answer(item, user)
            if resp is not None:
                answers_sel[idx] = (resp, "perfil")

        # ── Paso 2: preguntas no resueltas → Claude con CV ────────────────────
        sin_resp_items = [item for idx, item in enumerate(pending_all) if idx not in answers_sel]
        if sin_resp_items:
            cv_url  = user.get("cv_url") or user.get("CV_URL") or ""
            cv_text = _extract_cv_text(cv_url) if cv_url else ""
            llm_raw = _llm_answer_questions(sin_resp_items, user, cv_text=cv_text)
            _save_answers_to_cache(sin_resp_items, llm_raw)
            sin_idx = [idx for idx in range(len(pending_all)) if idx not in answers_sel]
            for local_i, global_i in enumerate(sin_idx):
                resp = llm_raw.get(str(local_i), "")
                if resp:
                    answers_sel[global_i] = (resp, "Claude")

        _NUMERIC_LABEL_KEYS_SEL = {
            "PRETENSION", "SUELDO", "RENTA", "SALARIO", "REMUNERACION", "LIQUID",
            "ANOS DE EXP", "AÑOS DE EXP", "ANOS EXP", "CUANTOS ANOS", "CUANTOS AÑO",
            "ANOS DE EXPERIENCIA", "AÑOS DE EXPERIENCIA",
        }

        def _is_numeric_sel(it: dict) -> bool:
            if it.get("type") == "number":
                return True
            if it.get("inputmode") in ("numeric", "decimal"):
                return True
            lbl = _norm((it.get("label") or "") + " " + (it.get("placeholder") or ""))
            return any(k in lbl for k in _NUMERIC_LABEL_KEYS_SEL)

        def _fallback_sel(it: dict) -> str:
            """Fallback cuando ni perfil ni Claude respondieron — nunca "Sí" para texto."""
            t = it.get("type", "text")
            if _is_numeric_sel(it):
                return re.sub(r"[^\d]", "", str(user.get("pretension_general") or "")) or str(user.get("experiencia") or "5")
            if t == "textarea":
                rv = str(user.get("resumen") or "")
                return rv[:400] if rv else f"Profesional con {str(user.get('experiencia') or '5')} años de experiencia."
            if t in ("tel", "phone"):
                return re.sub(r"\D", "", str(user.get("celular") or ""))[-9:]
            return ""

        for idx, item in enumerate(pending_all):
            try:
                el       = item["el"]
                kind     = item["kind"]
                inp_type = item["type"]
                resp, source = answers_sel.get(idx, (_fallback_sel(item), "fallback"))

                if _is_numeric_sel(item):
                    resp = re.sub(r"[^\d]", "", resp) or resp

                print(f"    [preg/{source}] [{idx}] type={inp_type} '{(item['label'] or inp_type)[:50]}' -> '{resp[:40]}'")

                if kind == "input":
                    driver.execute_script("""
                        var el = arguments[0], val = arguments[1];
                        Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(el, val);
                        el.dispatchEvent(new Event('input',  {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.dispatchEvent(new FocusEvent('blur', {bubbles: true}));
                    """, el, resp)
                else:
                    driver.execute_script("""
                        var el = arguments[0], val = arguments[1];
                        Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set.call(el, val);
                        el.dispatchEvent(new Event('input',  {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.dispatchEvent(new FocusEvent('blur', {bubbles: true}));
                    """, el, resp)
            except Exception:
                pass

        # --- Selects ---
        _SKIP_VALS = {"", "0", "-1", "null", "Selecciona", "Seleccione"}
        for sel in driver.find_elements(By.TAG_NAME, "select"):
            try:
                if not sel.is_displayed():
                    continue
                cur = sel.get_attribute("value") or ""
                if cur and cur not in _SKIP_VALS:
                    continue
                opts = sel.find_elements(By.TAG_NAME, "option")
                preg = _norm(_get_label(sel))
                resp = _respuesta(preg)
                resp_norm = _norm(resp)
                elegida_val = None
                for opt in opts:
                    ov = opt.get_attribute("value") or ""
                    if not ov or ov in _SKIP_VALS:
                        continue
                    if resp_norm in _norm(opt.text) or _norm(opt.text) in resp_norm:
                        elegida_val = ov; break
                if not elegida_val:
                    for opt in opts:
                        ov = opt.get_attribute("value") or ""
                        if ov and ov not in _SKIP_VALS:
                            elegida_val = ov; break
                if elegida_val:
                    driver.execute_script("""
                        var sel = arguments[0], val = arguments[1];
                        sel.value = val;
                        sel.dispatchEvent(new Event('change', {bubbles: true}));
                        sel.dispatchEvent(new Event('input',  {bubbles: true}));
                    """, sel, elegida_val)
            except Exception:
                pass

        print(f"    [trabajando] Preguntas respondidas: {len(grupos)} radios, {len(inputs)} inputs")

    except Exception as e:
        print(f"    [trabajando] Error respondiendo preguntas: {e}")


def _safe_click(driver, by, selector, timeout=8, label=""):
    """Click con reintentos — robusto ante re-renders de Vue."""
    for attempt in range(3):
        try:
            el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, selector)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.2)
            driver.execute_script("arguments[0].click();", el)
            if label:
                print(f"    [trabajando] Clickeado: {label}")
            return True
        except Exception:
            if attempt == 2:
                return False
            time.sleep(0.5)
    return False


def apply_trabajando_selenium(driver: webdriver.Chrome, job_url: str, user: dict = {}, resumen: str = "") -> "dict | bool":
    """
    Postula a un empleo en Trabajando.cl usando un driver ya logueado.
    Retorna dict {"ok": True, "empresa": ..., "descripcion": ...} en éxito, False en fallo.
    """
    import traceback
    try:
        # Verificar sesión antes de navegar — re-loguear si expiró
        uid = user.get("ID_USUARIO") or user.get("id_usuario", "")
        with _sessions_lock:
            sess_data = _sessions.get(f"trabajando_{uid}", {})
        if sess_data:
            _ensure_logged_in(driver, sess_data.get("email", ""), sess_data.get("password", ""))

        driver.get(job_url)
        time.sleep(3)

        # Si la navegación al empleo redirigió a login, re-loguear y reintentar
        if "ingresa-a-tu-cuenta" in driver.current_url and sess_data:
            print(f"    [trabajando] Redirigido a login al navegar al empleo — re-logueando")
            ok = _do_login(driver, sess_data.get("email", ""), sess_data.get("password", ""))
            if not ok:
                return False
            driver.get(job_url)
            time.sleep(3)

        print(f"    [trabajando] Cargado: {driver.current_url[:80]}")

        # Scrape empresa y descripcion desde la página del empleo
        empresa = ""
        descripcion = ""
        try:
            for sel in [".empresa", ".nombre-empresa", "[class*='empresa']",
                        ".company", "[class*='company']"]:
                els = [e for e in driver.find_elements(By.CSS_SELECTOR, sel)
                       if e.is_displayed() and e.text.strip()]
                if els:
                    empresa = els[0].text.strip()[:500]
                    break
        except Exception:
            pass
        try:
            for sel in ["[class*='descripcion-oferta']", "[class*='descripcion-empleo']",
                        ".job-description", "#descripcion", "[class*='descripcion']",
                        "[class*='description']"]:
                els = [e for e in driver.find_elements(By.CSS_SELECTOR, sel)
                       if e.is_displayed() and len(e.text.strip()) > 50]
                if els:
                    descripcion = els[0].text.strip()[:5000]
                    break
        except Exception:
            pass

        def _ok():
            return {"ok": True, "empresa": empresa, "descripcion": descripcion}

        # ── Paso 1: click botón inicial de postular ────────────────────────────
        clickeado = False
        for xp in [
            '//*[@id="columnaPostular"]/div/button[1]',
            '//button[contains(text(),"Postula")]',
            '//button[contains(translate(text(),"POSTULAR","postular"),"postular")]',
        ]:
            try:
                btn = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((By.XPATH, xp)))
                texto = btn.text.strip()
                # Si el botón ya dice que está postulado → retornar True de inmediato
                if any(s in texto.lower() for s in ["ya postulaste", "postulado", "aplicaste"]):
                    print(f"    [trabajando] Ya postulado (botón dice '{texto}')")
                    return _ok()
                driver.execute_script("arguments[0].click();", btn)
                print(f"    [trabajando] Click en '{texto}'")
                clickeado = True
                break
            except Exception:
                continue

        if not clickeado:
            print(f"    [trabajando] No se encontró botón de postular en {job_url[:70]}")
            return False

        time.sleep(2)

        # ── Paso 2a: Modal "Este empleo solicita un CV Trabajando.com" ─────────
        # Algunos empleos exigen CV interno. Por ahora guardamos y saltamos.
        # TODO: automatizar creación de CV interno de Trabajando.com
        try:
            WebDriverWait(driver, 3).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//*[contains(text(),'solicita un CV Trabajando')]")
                )
            )
            print(f"    [trabajando] Empleo requiere CV interno — guardando para despues")
            _safe_click(
                driver, By.XPATH,
                "//*[contains(text(),'Guardar empleo y postular')]",
                timeout=5, label="Guardar empleo"
            )
            return False
        except Exception:
            pass  # No apareció ese modal — continuar

        # ── Paso 2b: Modal "Vas a postular con tu archivo" ────────────────────
        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//*[contains(text(),'postular con tu archivo')]")
                )
            )
            print(f"    [trabajando] Modal CV detectado")
            time.sleep(0.8)  # esperar animación del modal

            # El modal se inserta al final del DOM — tomar el ÚLTIMO botón visible
            # que diga "Postular" y no esté en la columna principal
            clicked_modal = False
            for xp in [
                "//div[contains(@class,'modal') or contains(@class,'Modal')]//button[contains(.,'Postular')]",
                "//button[normalize-space(.)='Postular']",
                "//button[contains(.,'Postular') and not(ancestor::*[@id='columnaPostular'])]",
                "//button[contains(.,'Postular')]",
            ]:
                btns = [b for b in driver.find_elements(By.XPATH, xp) if b.is_displayed()]
                if btns:
                    btn_modal = btns[-1]  # el modal está al final del DOM
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn_modal)
                    time.sleep(0.2)
                    driver.execute_script("arguments[0].click();", btn_modal)
                    print(f"    [trabajando] Clickeado: Postular (modal CV) '{btn_modal.text.strip()}'")
                    clicked_modal = True
                    break

            if not clicked_modal:
                print(f"    [trabajando] ! No se encontró botón Postular en modal CV")
            time.sleep(2)
        except Exception:
            pass  # Modal CV no apareció — flujo sin CV o ya confirmado

        # ── Paso 3: Modal con preguntas ────────────────────────────────────────
        try:
            xp_preg = '//*[@id="modalConfirmarPreguntas"]/div/div/div[2]/div/div[2]/div[3]/button'
            if _safe_click(driver, By.XPATH, xp_preg, timeout=5, label="Siguiente (preguntas)"):
                time.sleep(2)

                # Responder todas las preguntas para habilitar el botón de envío
                _responder_preguntas(driver, user)

                # Esperar dinámicamente hasta 6s a que Vue habilite el botón
                for _ in range(12):
                    time.sleep(0.5)
                    btns_ok = [b for b in driver.find_elements(
                        By.XPATH, '//div[@id="cabeceraPreguntasEscritorio"]//button[not(@disabled)]'
                    ) if b.is_displayed()]
                    if btns_ok:
                        break

                # Esperar que el botón de envío se habilite y clickearlo
                enviado = False
                for xp_env in [
                    '//div[@id="cabeceraPreguntasEscritorio"]/div[3]/button[not(@disabled)]',
                    '//div[@id="cabeceraPreguntasEscritorio"]//button[not(@disabled)]',
                    '//*[@id="modalConfirmarPreguntas"]//button[not(@disabled) and (contains(text(),"Postular") or contains(text(),"Enviar") or contains(text(),"Confirmar") or contains(text(),"Siguiente"))]',
                    '//button[not(@disabled) and (contains(text(),"Postular") or contains(text(),"Enviar")) and not(ancestor::*[@id="columnaPostular"])]',
                ]:
                    if _safe_click(driver, By.XPATH, xp_env, timeout=6, label="Enviar preguntas"):
                        enviado = True
                        break
                if enviado:
                    time.sleep(2)
                    print(f"    [trabajando] OK Postulado (envio preguntas exitoso)")
                    return _ok()
                else:
                    print(f"    [trabajando] No se encontro boton de envio habilitado tras preguntas")
        except Exception as ep:
            print(f"    [trabajando] Modal preguntas: {ep}")

        # ── Paso 4: Modal de confirmación directa ─────────────────────────────
        try:
            xp_conf = '//*[@id="modalConfirmarPostulacion"]/div/div/div[2]/div/div[2]/div[3]/button'
            if _safe_click(driver, By.XPATH, xp_conf, timeout=5, label="Confirmar postulacion"):
                time.sleep(2)
                print(f"    [trabajando] OK Postulado (confirmacion directa)")
                return _ok()
        except Exception:
            pass

        time.sleep(3)

        # ── Verificar resultado ────────────────────────────────────────────────
        # 1. El botón "Postula fácil" cambia a "Ya postulaste" si fue exitoso
        try:
            texto_btn = driver.find_element(
                By.XPATH, '//*[@id="columnaPostular"]//button'
            ).text.strip().lower()
            print(f"    [trabajando] Estado boton postular: '{texto_btn}'")
            if any(s in texto_btn for s in ["ya postulaste", "postulado", "aplicaste", "postulacion enviada"]):
                print(f"    [trabajando] OK Postulado")
                return _ok()
        except Exception:
            pass

        # 2. Buscar señales de éxito en el contenido de la página
        content = driver.page_source.lower()
        exito = any(s in content for s in [
            "postulación enviada", "te has postulado", "gracias por postular",
            "postulaste exitosamente", "aplicación enviada",
        ])
        print(f"    [trabajando] {'OK Postulado' if exito else 'Sin confirmar exito'} -> {driver.current_url[:60]}")
        return _ok() if exito else False

    except Exception as e:
        print(f"    [trabajando] Error al postular: {e}")
        traceback.print_exc()
        return False


# ─── GESTIÓN DE CUENTAS ───────────────────────────────────────────────────────

def get_or_create_account(user: dict, portal: str) -> dict | None:
    uid = user.get("ID_USUARIO") or user.get("id_usuario", "")

    cuenta = bq.get_portal_account(uid, portal)
    if cuenta:
        print(f"  -> Cuenta {portal} existente para {uid}")
        return cuenta

    nombre_completo = user.get("NOMBRE") or user.get("nombre") or ""
    partes = nombre_completo.strip().split()
    nombre   = partes[0] if partes else "Usuario"
    apellido = " ".join(partes[1:]) if len(partes) > 1 else "Jobs"
    celular  = user.get("CELULAR") or user.get("celular") or ""
    clave    = _generar_clave()

    nombre_slug   = re.sub(r"[^a-z0-9]", "", nombre.lower())
    apellido_slug = re.sub(r"[^a-z0-9]", "", (partes[1] if len(partes) > 1 else "").lower())[:3]
    codigo        = secrets.token_hex(3)
    portal_email  = f"{nombre_slug}.{apellido_slug}{codigo}@gmail.com"

    ok = False
    if portal == "trabajando":
        print(f"  -> Creando cuenta en Trabajando.cl para {nombre} {apellido} ({portal_email})")
        ok = crear_cuenta_trabajando(nombre, apellido, celular, portal_email, clave)

    elif portal == "chiletrabajos":
        # El módulo genera email/clave y guarda en CUENTAS_PORTALES por sí mismo
        from chiletrabajos.crear_cuenta import crear_cuenta_chiletrabajos
        ok = crear_cuenta_chiletrabajos(uid, user)
        if ok:
            return bq.get_portal_account(uid, portal)
        return None

    elif portal == "indeed":
        # Indeed requiere email real (OTP de verificación) — delegamos al módulo
        from indeed.crear_cuenta import crear_cuenta_indeed
        ok = crear_cuenta_indeed(uid, user)
        if ok:
            return bq.get_portal_account(uid, portal)
        return None

    if not ok:
        return None

    bq.save_portal_account(uid, portal, portal_email, clave)
    print(f"  -> Cuenta {portal} creada y guardada para {uid}")
    return {"email": portal_email, "password": clave}


if __name__ == "__main__":
    user = {
        "ID_USUARIO": "jobs2",
        "NOMBRE": "Bastian Alonso Alfaro Lazo",
        "EMAIL": "bastian.alfaro@gmail.com",
        "CELULAR": "",
    }
    resultado = get_or_create_account(user, "trabajando")
    print("Resultado:", resultado)
