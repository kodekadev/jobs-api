"""
Laborum.cl — Postulaciones automáticas con sesión autenticada.

Flujo:
  1. Carga cookies de BigQuery e inyecta en el contexto
  2. Por cada empleo: navega a la URL del empleo
  3. Click "Postular" / "Aplicar"
  4. Confirmar si hay modal
  5. Guardar resultado en BigQuery EMPLEOS

Uso:
    from laborum.postular import postular_empleos_lab
    ok = postular_empleos_lab(user_id, user, empleos, max_n=10)
"""
import os
import sys
import re
import time
import random
import datetime

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bq

BASE_URL   = "https://www.laborum.cl"
LOGIN_URL  = f"{BASE_URL}/login"
SIGNIN_URL = f"{BASE_URL}/signin"
PORTAL_ID  = "laborum"

_BTN_POSTULAR = [
    "button:has-text('Postulación rápida')",
    "button:has-text('Postular')",
    "button:has-text('Aplicar')",
    "button:has-text('Me interesa')",
    "button:has-text('Inscribirme')",
    "[data-testid*='apply']",
    "[data-testid*='postul']",
    ".btn-apply",
]

_SUELDO_DEFAULT = 2_500_000  # fallback si PRETENSION_GENERAL no está en BQ

_CONFIRM_SIGNALS = [
    "postulación enviada", "te has postulado", "gracias",
    "aplicación enviada", "postulación registrada", "ya postulaste",
    "enviada con éxito", "tu postulación",
]
_YA_POSTULADO_SIGNALS = [
    "ya aplicaste a esta oferta", "ya te postulaste", "ya aplicaste",
    "ya has aplicado", "ya postulaste", "ya eres postulante",
    "ya postulaste a esta", "candidato a esta oferta",
]
_ACTIVATION_SIGNALS = [
    "activa tu cuenta",
    "reenviarme el email",
    "verifica tu cuenta",
    "verificar tu cuenta",
    "confirmar tu correo",
]


def _esta_logueado(page) -> bool:
    cur     = page.url.lower()
    content = page.content().lower()
    if any(s in cur for s in ("login", "signin", "/acceso")):
        return False
    return any(s in content for s in [
        "cerrar sesión", "mi cuenta", "salir", "logout",
        "mis postulaciones", "editar perfil",
    ])


def _type_react(page, selector: str, value: str, timeout: int = 5000):
    loc = page.locator(selector).first
    loc.wait_for(state="visible", timeout=timeout)
    loc.triple_click()
    page.wait_for_timeout(50)
    page.keyboard.type(value, delay=40)


def _set_react_input(page, selector: str, value: str) -> bool:
    """Setea un input React/Angular via JS nativo (evita problemas con onChange)."""
    return page.evaluate("""([sel, val]) => {
        const el = document.querySelector(sel);
        if (!el) return false;
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
        ).set;
        setter.call(el, val);
        el.dispatchEvent(new Event('input',  {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        el.dispatchEvent(new Event('blur',   {bubbles: true}));
        return true;
    }""", [selector, value])


def _click_submit(page) -> bool:
    for sel in [
        "button[type='submit']:visible",
        "button:has-text('Continuar'):visible",
        "button:has-text('Siguiente'):visible",
        "button:has-text('Ingresar'):visible",
        "button:has-text('Iniciar sesión'):visible",
        "button:has-text('Entrar'):visible",
    ]:
        try:
            btn = page.locator(sel).first
            if btn.count() and btn.is_visible(timeout=1500):
                btn.click()
                return True
        except Exception:
            pass
    return False


def _login(page, email: str, password: str) -> bool:
    """
    Login en Laborum. Soporta tanto flujo clásico (email+password) como OTP
    (email → código enviado por correo).
    """
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    print(f"  [lab-login] URL: {page.url[:80]}")

    # Toggle a login si la página muestra registro en vez de login
    for toggle_sel in [
        "button:has-text('Ya tengo cuenta')",
        "a:has-text('Iniciar sesión')",
        "a:has-text('Login')",
    ]:
        try:
            tgl = page.locator(toggle_sel).first
            if tgl.count() > 0 and tgl.is_visible(timeout=2000):
                tgl.click()
                page.wait_for_timeout(1000)
                break
        except Exception:
            pass

    # ── Paso 1: Ingresar email ────────────────────────────────────────────────
    em_ok = _set_react_input(page, "input[type='email'], input[name='email']", email)
    if not em_ok:
        try:
            _type_react(page, "input[type='email'], input[name='email']", email)
            em_ok = True
        except Exception:
            pass

    if not em_ok:
        print("  [lab-login] No se encontró campo de email")
        return False

    page.wait_for_timeout(300)

    # ── Paso 2: Password si hay campo (flujo clásico) o Submit ───────────────
    pw_field = page.locator("input[type='password']").first
    if pw_field.count() and pw_field.is_visible(timeout=1500):
        # Flujo clásico: email + password en la misma página
        _set_react_input(page, "input[type='password']", password)
        page.wait_for_timeout(200)
        _click_submit(page)
        page.wait_for_timeout(5000)
        print(f"  [lab-login] URL post-password: {page.url[:80]}")
    else:
        # Flujo de 2 pasos: submit email → puede aparecer campo password (no OTP)
        try:
            cont = page.locator("button:has-text('Continuar'):visible").first
            if cont.count() and cont.is_visible(timeout=1000):
                cont.click()
            else:
                _click_submit(page)
        except Exception:
            _click_submit(page)
        page.wait_for_timeout(2500)
        print(f"  [lab-login] URL post-email: {page.url[:80]}")

        # Después del submit puede aparecer el campo de password (flujo 2 pasos)
        pw_field2 = page.locator("input[type='password']").first
        if pw_field2.count() and pw_field2.is_visible(timeout=2000):
            print(f"  [lab-login] Password field apareció — flujo 2 pasos")
            _set_react_input(page, "input[type='password']", password)
            page.wait_for_timeout(200)
            _click_submit(page)
            page.wait_for_timeout(5000)
            print(f"  [lab-login] URL post-password: {page.url[:80]}")

    # ── Paso 3: Detectar si pide OTP ─────────────────────────────────────────
    content = page.content().lower()
    _OTP_SIGNALS = (
        "código de acceso", "código temporal", "access code", "código de verificación",
        "revisa tu correo", "check your email", "ingresa el código", "enter the code",
        "one-time", "otp",
    )
    otp_mode = any(s in content for s in _OTP_SIGNALS)

    # También detectar por campo tipo text/number sin email/password
    if not otp_mode:
        cod_field = page.locator(
            "input[name='code'], input[name='otp'], "
            "input[autocomplete='one-time-code'], input[type='number']"
        ).first
        otp_mode = cod_field.count() > 0 and cod_field.is_visible(timeout=1500)

    if otp_mode:
        print(f"  [lab-login] OTP detectado — esperando código en {email}...")
        from email_verifier import esperar_verificacion
        resultado = esperar_verificacion(email, timeout=30, code_only=True, sender_filter="laborum")
        if not resultado:
            print("  [lab-login] No llegó código OTP")
            return False

        codigo = re.sub(r'\D', '', str(resultado))[:8]
        print(f"  [lab-login] Código recibido: {codigo}")

        # Esperar a que el form OTP esté en el DOM
        page.wait_for_timeout(2000)

        # Ingresar código — keyboard typing (dispara onChange de React correctamente)
        _OTP_SELS = [
            "#codigo2fa",                   # Laborum: id específico del campo OTP
            "input[name='codigo']",         # Laborum: name del campo OTP
            "input[name='code']", "input[name='otp']",
            "input[autocomplete='one-time-code']",
            "form#form-2fa input[type='text']",
            "input[type='number']",
            "input[type='text']",
            "input:not([type='email']):not([type='hidden']):not([type='password'])",
        ]

        # Inputs individuales (un dígito por caja)
        inputs_otp = page.locator("input[autocomplete='one-time-code']")
        if inputs_otp.count() > 1:
            for i, digit in enumerate(codigo):
                try:
                    inputs_otp.nth(i).click()
                    page.keyboard.type(digit, delay=60)
                    page.wait_for_timeout(80)
                except Exception:
                    break
        else:
            typed = False
            for sel in _OTP_SELS:
                try:
                    loc = page.locator(sel).first
                    if not loc.count() or not loc.is_visible(timeout=2000):
                        continue
                    loc.click()
                    loc.click(click_count=3)  # seleccionar todo (triple_click no existe)
                    page.wait_for_timeout(100)
                    page.keyboard.type(codigo, delay=60)
                    typed = True
                    print(f"  [lab-login] Código tipeado en '{sel}'")
                    break
                except Exception as _e:
                    print(f"  [lab-login] error con '{sel}': {_e}")
                    continue
            if not typed:
                print("  [lab-login] No se encontró campo OTP")
                return False

        # Esperar a que React valide antes de submit
        page.wait_for_timeout(600)

        # Click "Ingresar" (submit del código)
        clicked = False
        for sel in [
            "button[data-testid='login-2fa-submit-button']:visible",
            "form#form-2fa button[type='submit']:visible",
            "button:has-text('Ingresar'):visible",
            "button[type='submit']:visible",
        ]:
            try:
                btn = page.locator(sel).first
                if btn.count() and btn.is_visible(timeout=1500) and btn.is_enabled():
                    btn.click()
                    clicked = True
                    print(f"  [lab-login] Submit OTP clickeado")
                    break
            except Exception:
                continue
        if not clicked:
            print("  [lab-login] Botón Ingresar no disponible")
            return False

        page.wait_for_timeout(5000)
        print(f"  [lab-login] URL post-OTP: {page.url[:80]}")

    logueado = _esta_logueado(page)
    print(f"  [lab-login] Logueado: {logueado}")
    return logueado


def _llenar_sueldo(page, salario: int) -> bool:
    """Llena el form de sueldo pretendido si está presente. Retorna True si encontró y llenó."""
    sal_sel = "#salarioPretendido, input[name='salarioPretendido']"
    try:
        sal_field = page.locator(sal_sel).first
        if not sal_field.count() or not sal_field.is_visible(timeout=3000):
            return False
    except Exception:
        return False

    sal_str = str(salario)
    ok = _set_react_input(page, "#salarioPretendido", sal_str)
    if not ok:
        try:
            sal_field.click()
            sal_field.click(click_count=3)
            page.keyboard.type(sal_str, delay=40)
            ok = True
        except Exception:
            pass
    if ok:
        page.wait_for_timeout(400)
        print(f"    [lab] sueldo ingresado: {sal_str}")
    return ok


def _set_react_textarea(page, selector: str, value: str) -> bool:
    """Setea un textarea React via JS nativo (dispara onChange correctamente)."""
    return page.evaluate("""([sel, val]) => {
        const el = document.querySelector(sel);
        if (!el) return false;
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLTextAreaElement.prototype, 'value'
        ).set;
        setter.call(el, val);
        el.dispatchEvent(new Event('input',  {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        el.dispatchEvent(new Event('blur',   {bubbles: true}));
        return true;
    }""", [selector, value])


def _responder_preguntas_lab(page, user: dict, job_title: str = "", cv_text: str = "") -> bool:
    """
    Detecta y responde el formulario form#form-preguntas de Laborum.
    Retorna True si no hay preguntas o si se respondieron y enviaron.
    """
    try:
        if not page.locator("form#form-preguntas").count():
            return True
        if not page.locator("form#form-preguntas").is_visible(timeout=2000):
            return True
    except Exception:
        return True

    print(f"    [lab] preguntas de selección detectadas")

    questions = page.evaluate("""
() => {
    const form = document.querySelector('form#form-preguntas');
    if (!form) return [];
    const result = [];

    // ── Textareas ──────────────────────────────────────────────────────────
    form.querySelectorAll('textarea').forEach(ta => {
        if (!ta.name || !ta.id) return;
        let labelText = '';
        const lbl = form.querySelector('[for="' + ta.id + '"]');
        if (lbl) {
            const clone = lbl.cloneNode(true);
            clone.querySelectorAll('sup').forEach(s => s.remove());
            labelText = clone.innerText.trim();
        }
        if (!labelText) labelText = ta.getAttribute('label') || ta.placeholder || '';
        result.push({ kind: 'textarea', id: ta.id, name: ta.name, label: labelText, options: [] });
    });

    // ── Radio buttons ──────────────────────────────────────────────────────
    const seenRadio = new Set();
    form.querySelectorAll('input[type="radio"]').forEach(r => {
        if (seenRadio.has(r.name)) return;
        seenRadio.add(r.name);
        // Texto de la pregunta: legend[for^="radiobutton-NAME"], o div padre con attr label
        let labelText = '';
        const legend = form.querySelector('legend[for^="radiobutton-' + r.name + '"]');
        if (legend) labelText = legend.innerText.trim();
        if (!labelText) {
            const pDiv = r.closest('div[label]');
            if (pDiv) labelText = pDiv.getAttribute('label') || '';
        }
        const opts = Array.from(
            form.querySelectorAll('input[type="radio"][name="' + r.name + '"]')
        ).map(radio => {
            const lbl = form.querySelector('label[for="' + radio.id + '"]');
            return {
                value: radio.value,
                label: lbl ? lbl.innerText.trim() : (radio.getAttribute('aria-label') || radio.value),
                id: radio.id,
            };
        });
        result.push({ kind: 'radio', id: r.id, name: r.name, label: labelText.trim(), options: opts });
    });

    // ── Selects ────────────────────────────────────────────────────────────
    form.querySelectorAll('select').forEach(sel => {
        if (!sel.name) return;
        let labelText = '';
        const lbl = form.querySelector('label[for="' + sel.id + '"]') ||
                    form.querySelector('[for="' + sel.id + '"]');
        if (lbl) labelText = lbl.innerText.trim();
        if (!labelText) {
            const pDiv = sel.closest('div[label]');
            if (pDiv) labelText = pDiv.getAttribute('label') || '';
        }
        const opts = Array.from(sel.options)
            .filter(o => o.value !== '')
            .map(o => ({ value: o.value, label: o.text.trim(), id: '' }));
        result.push({ kind: 'select', id: sel.id || '', name: sel.name, label: labelText.trim(), options: opts });
    });

    return result;
}
""")

    if not questions:
        print(f"    [lab] no se pudieron extraer preguntas — omitiendo")
        return True

    print(f"    [lab] {len(questions)} pregunta(s): {[q.get('kind','?') + ':' + q['label'][:35] for q in questions]}")

    from portal_accounts import _llm_answer_questions, _standard_answer

    answers: dict[int, str] = {}
    unresolved_idx: list[int] = []
    llm_items: list[dict] = []

    for i, q in enumerate(questions):
        kind = q.get("kind", "textarea")
        item = {
            "label": q["label"],
            "type": kind,
            "options": [o["label"] for o in q.get("options", [])],
            "placeholder": "",
        }
        std = _standard_answer(item, user)
        if std:
            answers[i] = std
        else:
            unresolved_idx.append(i)
            llm_items.append({
                "label": q["label"],
                "type": kind,
                "options": [{"text": o["label"], "value": o["value"]} for o in q.get("options", [])],
                "placeholder": "",
            })

    if unresolved_idx:
        llm_ans = _llm_answer_questions(llm_items, user, cv_text=cv_text, job_title=job_title)
        for llm_i, orig_i in enumerate(unresolved_idx):
            ans = llm_ans.get(str(llm_i), "")
            if ans:
                answers[orig_i] = ans

    for i, q in enumerate(questions):
        ans = answers.get(i, "")
        kind = q.get("kind", "textarea")

        if not ans:
            # Fallback Si/No para radio/select de 2 opciones
            opts = q.get("options", [])
            if kind in ("radio", "select") and len(opts) == 2:
                opts_lower = [o["label"].lower() for o in opts]
                if "si" in opts_lower or "sí" in opts_lower:
                    ans = next(o["label"] for o in opts if o["label"].lower() in ("si", "sí"))
                    print(f"    [lab] default 'Sí' para: '{q['label'][:50]}'")
            if not ans:
                print(f"    [lab] sin respuesta para: '{q['label'][:50]}'")
                continue

        if kind == "textarea":
            _set_react_textarea(page, f'#{q["id"]}', ans[:1990])

        elif kind == "radio":
            ans_norm = ans.strip().lower()
            opts = q.get("options", [])
            matched = next(
                (o for o in opts if o["label"].lower() == ans_norm or ans_norm in o["label"].lower()),
                opts[0] if opts else None,
            )
            if matched:
                page.evaluate("""([name, val]) => {
                    const r = document.querySelector(
                        'input[type="radio"][name="' + name + '"][value="' + val + '"]'
                    );
                    if (r) { r.checked = true; r.click(); r.dispatchEvent(new Event('change', {bubbles:true})); }
                }""", [q["name"], matched["value"]])

        elif kind == "select":
            ans_norm = ans.strip().lower()
            opts = q.get("options", [])
            matched = next(
                (o for o in opts if o["label"].lower() == ans_norm or ans_norm in o["label"].lower()),
                None,
            )
            if matched:
                sel_id = q.get("id") or ""
                sel_name = q["name"]
                page.evaluate("""([sid, sname, val]) => {
                    const el = (sid && document.getElementById(sid))
                             || document.querySelector('select[name="' + sname + '"]');
                    if (el) {
                        el.value = val;
                        el.dispatchEvent(new Event('change', {bubbles:true}));
                    }
                }""", [sel_id, sel_name, matched["value"]])

        print(f"    [lab] {kind} '{q['label'][:40]}' → '{ans[:50]}'")

    page.wait_for_timeout(500)

    submitted = False
    for sel in [
        "button[form='form-preguntas']",          # botón externo al form (patrón Laborum)
        "form#form-preguntas button[type='submit']",
        "button:has-text('Responder')",
        "button:has-text('Enviar postulación')",
        "button:has-text('Continuar')",
        "button:has-text('Siguiente')",
        "button:has-text('Enviar')",
        "button[type='submit']",
    ]:
        try:
            btn = page.locator(sel).first
            if not btn.count():
                continue
            btn.scroll_into_view_if_needed()
            page.wait_for_timeout(200)
            if btn.is_enabled():
                btn.click()
                submitted = True
                print(f"    [lab] submit preguntas clickeado ({sel})")
                page.wait_for_timeout(2500)
                break
        except Exception:
            pass

    if not submitted:
        submitted = page.evaluate("""() => {
            // El botón puede estar fuera del <form> con atributo form="form-preguntas"
            const extBtn = document.querySelector('button[form="form-preguntas"]');
            if (extBtn && !extBtn.disabled) { extBtn.click(); return true; }
            const form = document.querySelector('form#form-preguntas');
            if (!form) return false;
            const btn = form.querySelector('button[type="submit"]');
            if (btn && !btn.disabled) { btn.click(); return true; }
            for (const b of form.querySelectorAll('button')) {
                if (!b.disabled && b.offsetParent !== null) { b.click(); return true; }
            }
            return false;
        }""")
        if submitted:
            print(f"    [lab] submit preguntas via JS")
            page.wait_for_timeout(2500)

    if not submitted:
        print(f"    [lab] no se encontró botón submit para preguntas")
        return False
    return True


def _extraer_descripcion_lab(page) -> str:
    """Extrae la descripción del empleo desde la página de detalle de Laborum."""
    try:
        return page.evaluate("""() => {
            // Selector estable: id="descripcion-aviso" (presente en HTML real de Laborum)
            const byId = document.querySelector('#descripcion-aviso');
            if (byId) {
                const t = byId.innerText.trim();
                if (t.length > 80) return t.slice(0, 4000);
            }
            // Fallback: contenedor region con aria-labelledby="descripcion-aviso"
            const byRegion = document.querySelector('[aria-labelledby="descripcion-aviso"]');
            if (byRegion) {
                const t = byRegion.innerText.trim();
                if (t.length > 80) return t.slice(0, 4000);
            }
            // Fallback: div más largo con >200 chars que no sea nav/header/footer
            const divs = Array.from(document.querySelectorAll('main div, article div, section div'));
            let best = '';
            for (const d of divs) {
                if (d.children.length > 20) continue;
                const t = d.innerText.trim();
                if (t.length > best.length && t.length > 200 && t.length < 8000) best = t;
            }
            return best.slice(0, 4000);
        }""") or ""
    except Exception:
        return ""


def _intentar_activar(page, email: str, timeout: int = 60) -> bool:
    """Lee el email de activación de Laborum y abre el link. Deja la cuenta lista para el próximo run."""
    try:
        from email_verifier import esperar_verificacion
        verif = esperar_verificacion(email, timeout=timeout, sender_filter="laborum")
        if not verif or not verif.startswith("http"):
            print(f"    [lab] No llegó email de activación para {email}")
            return False
        print(f"    [lab] Activando cuenta: {verif[:80]}...")
        page.goto(verif, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        print(f"    [lab] Cuenta activada: {page.url[:80]}")
        return True
    except Exception as e:
        print(f"    [lab] Error activando cuenta: {e}")
        return False


def _postular_uno(page, empleo: dict, salario: int = _SUELDO_DEFAULT,
                  user: dict = None, cv_text: str = "", email: str = "") -> tuple[bool, str]:
    """Retorna (ok, motivo). motivo='ya_postulado_previamente' si ya se había aplicado."""
    url    = empleo.get("link", "")
    titulo = empleo.get("titulo", url)[:60]

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        time.sleep(random.uniform(1.5, 2.5))
    except Exception as e:
        print(f"    [lab] timeout cargando {url[:60]}: {e}")
        return False, "timeout"

    # Extraer descripción desde la página de detalle (siempre, listing no la trae)
    if not empleo.get("descripcion"):
        desc = _extraer_descripcion_lab(page)
        if desc:
            empleo["descripcion"] = desc
            print(f"    [lab] descripción extraída ({len(desc)} chars)")

    # Detectar si la página ya muestra "ya aplicaste" antes de intentar
    _pre_content = page.content().lower()
    if any(s in _pre_content for s in _YA_POSTULADO_SIGNALS):
        return False, "ya_postulado_previamente"

    # Caso 1: El form de sueldo ya está visible en la página (Postulación rápida inline)
    sal_inline = False
    try:
        if page.locator("#salarioPretendido").first.count() and \
           page.locator("#salarioPretendido").first.is_visible(timeout=1500):
            sal_inline = True
    except Exception:
        pass

    if sal_inline:
        _llenar_sueldo(page, salario)
        # Botón submit del form de sueldo
        for sel in [
            "button[form='form-salario-pretendido'][type='submit']:visible",
            "button:has-text('Postulación rápida')[type='submit']:visible",
            "button:has-text('Postulación rápida'):visible",
        ]:
            try:
                btn = page.locator(sel).first
                if btn.count() and btn.is_visible(timeout=2000) and btn.is_enabled():
                    btn.click()
                    print(f"    [lab] submit sueldo en {titulo}")
                    page.wait_for_timeout(2500)
                    break
            except Exception:
                pass
    else:
        # Caso 2: Hay un botón de postular que hay que clickear primero
        clicked = False
        for sel in _BTN_POSTULAR:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0 and btn.is_visible(timeout=2000):
                    btn.click()
                    clicked = True
                    print(f"    [lab] click '{btn.inner_text()[:30]}' en {titulo}")
                    break
            except Exception:
                pass

        if not clicked:
            print(f"    [lab] sin botón postular en {titulo}")
            return False, "sin_boton"

        page.wait_for_timeout(2000)

        # Si aparece form de sueldo tras el click, llenarlo y submitear
        try:
            if page.locator("#salarioPretendido").first.count() and \
               page.locator("#salarioPretendido").first.is_visible(timeout=2000):
                _llenar_sueldo(page, salario)
                page.wait_for_timeout(400)
                for sel in [
                    "button[form='form-salario-pretendido'][type='submit']:visible",
                    "button:has-text('Postulación rápida')[type='submit']:visible",
                    "button:has-text('Postulación rápida'):visible",
                    "button:has-text('Confirmar'):visible",
                    "button:has-text('Enviar postulación'):visible",
                    "button[type='submit']:visible",
                ]:
                    try:
                        btn = page.locator(sel).first
                        if btn.count() and btn.is_visible(timeout=1500) and btn.is_enabled():
                            btn.click()
                            page.wait_for_timeout(2500)
                            break
                    except Exception:
                        pass
        except Exception:
            pass

        # Confirmar modal si aparece (sin sueldo)
        for confirm_sel in [
            "button:has-text('Confirmar'):visible",
            "button:has-text('Enviar postulación'):visible",
            "button:has-text('Enviar'):visible",
        ]:
            try:
                btn = page.locator(confirm_sel).first
                if btn.count() > 0 and btn.is_visible(timeout=1500):
                    btn.click()
                    page.wait_for_timeout(2000)
                    break
            except Exception:
                pass

    # Responder preguntas de selección si aparecen
    if user:
        _responder_preguntas_lab(page, user, job_title=titulo, cv_text=cv_text)

    content = page.content().lower()

    # Detectar "ya postulado" post-click
    if any(s in content for s in _YA_POSTULADO_SIGNALS):
        return False, "ya_postulado_previamente"

    # Detectar modal de activación de cuenta — falso positivo sin este check
    if any(s in content for s in _ACTIVATION_SIGNALS):
        if email:
            print(f"    [lab] Cuenta {email} no activada — intentando activar para próximo run...")
            _intentar_activar(page, email)
        return False, "cuenta_no_activada"

    ok = any(s in content for s in _CONFIRM_SIGNALS)
    if not ok:
        ok = "login" not in page.url.lower() and "signin" not in page.url.lower()
    return ok, "" if ok else "sin_confirmacion"


def buscar_y_postular_lab(user_id: str, user: dict, cargos: list, ubicacion: str, max_n: int = 10) -> int:
    """
    Abre UNA sesión de browser para Laborum, busca empleos, filtra y aplica.
    Retorna el número de postulaciones exitosas.
    """
    if not cargos:
        return 0

    cuenta = bq.get_portal_account(user_id, PORTAL_ID)
    if not cuenta:
        print(f"  [lab-post] Sin cuenta para {user_id}")
        return 0

    email    = cuenta.get("email", "")
    password = cuenta.get("password", "")

    try:
        from playwright.sync_api import sync_playwright
        from portal_accounts import _make_pw_context, _new_stealth_page
        from portal_accounts import job_aplica_al_usuario
        from scraper import _scrape_laborum
    except ImportError:
        print("  [lab-post] Playwright no disponible")
        return 0

    _ident = user.get("EMAIL") or user.get("NOMBRE") or user_id
    print(f"  [lab-post] Iniciando para {_ident}")
    modo_revision = bq.get_modo_revision(user_id)
    print(f"  [lab-post] Modo: {'REVISIÓN (guardará para aprobar)' if modo_revision else 'AUTOPILOT (postula directo)'}")

    ok_count = 0

    try:
        _, browser, ctx, _ = _make_pw_context()
        page = _new_stealth_page(ctx)
        try:

            # Inyectar cookies (si hay)
            cookies = bq.get_portal_cookies(user_id, PORTAL_ID)
            if cookies:
                ctx.add_cookies([c for c in cookies if isinstance(c, dict) and "name" in c and "value" in c])
                print(f"  [lab-post] {len(cookies)} cookies inyectadas")

            # Verificar sesión con cookies
            page.goto(f"{BASE_URL}/mi-cuenta", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            if not _esta_logueado(page):
                # Cookies vencidas o sin cookies — limpiar y logearse con credenciales
                print(f"  [lab-post] Cookies inválidas, intentando login con credenciales...")
                try:
                    ctx.clear_cookies()
                except Exception:
                    pass
                if not _login(page, email, password):
                    return 0

            print(f"  [lab-post] Sesión activa — buscando empleos...")

            # Ya postulados (todos, para deduplicar)
            ya_postulados = bq.get_applied_job_ids(user_id, days=60)

            # Scrape + filtro por cada cargo
            empleos_filtrados = []
            vistos: set = set()
            for cargo in cargos:
                if len(empleos_filtrados) >= (max_n * 3 if modo_revision else max_n):
                    break
                nuevos = _scrape_laborum(page, cargo, ubicacion, max_n * 3)
                for emp in nuevos:
                    eid = emp.get("id") or emp.get("link", "")
                    if not eid or eid in vistos or eid in ya_postulados:
                        continue
                    vistos.add(eid)
                    aplica, motivo = job_aplica_al_usuario(emp.get("titulo", ""), emp.get("empresa", ""), user)
                    if aplica:
                        empleos_filtrados.append(emp)
                    else:
                        print(f"    [lab] skip '{emp.get('titulo','')[:45]}' — {motivo}")
                    if len(empleos_filtrados) >= (max_n * 3 if modo_revision else max_n):
                        break

            print(f"  [lab-post] {len(empleos_filtrados)} empleos relevantes")

            # Modo revisión: guardar todos como pendientes y salir
            if modo_revision:
                saved = bq.save_pending_jobs(user_id, PORTAL_ID, empleos_filtrados)
                print(f"  [lab-post] {saved} empleos guardados para revisión:")
                for pj in empleos_filtrados:
                    print(f"    → {pj['titulo'][:70]}")
                bq.save_portal_cookies(user_id, PORTAL_ID, ctx.cookies())
                return 0

            salario = int(user.get("PRETENSION_GENERAL") or _SUELDO_DEFAULT)

            cv_text = ""
            try:
                from portal_accounts import _extract_cv_text
                _cv_url = user.get("CV_URL") or user.get("cv_url") or ""
                cv_text = _extract_cv_text(_cv_url) or ""
            except Exception:
                pass

            for emp in empleos_filtrados[:max_n]:
                if ok_count >= max_n:
                    break
                url = emp.get("link", "")
                if not url:
                    continue

                ok, motivo = _postular_uno(page, emp, salario, user=user, cv_text=cv_text, email=email)

                if motivo == "ya_postulado_previamente":
                    ya_postulados.add(url)
                    print(f"    ~ [lab] ya postulado antes: {emp.get('titulo','')[:50]}")
                else:
                    if ok:
                        bq.save_jobs([{
                            "id_empleo":         url,
                            "id_usuario":        user_id,
                            "titulo_empleo":     emp.get("titulo", ""),
                            "cargo":             emp.get("cargo", ""),
                            "Fecha_Postulacion": datetime.datetime.utcnow().isoformat(),
                            "empresa":           emp.get("empresa", ""),
                            "descripcion":       emp.get("descripcion", ""),
                            "link":              url,
                            "portal":            PORTAL_ID,
                        }])
                        ok_count += 1
                        ya_postulados.add(url)
                        print(f"    [lab] postulado: {emp.get('titulo','')[:50]}")
                    else:
                        print(f"    [lab] falló ({motivo}): {emp.get('titulo','')[:50]}")

                time.sleep(random.uniform(3, 6))

            bq.save_portal_cookies(user_id, PORTAL_ID, ctx.cookies())
        finally:
            try:
                browser.close()
            except Exception:
                pass

    except Exception as e:
        import traceback
        print(f"  [lab-post] Error: {e}")
        traceback.print_exc()

    print(f"  [lab-post] {ok_count} postulaciones OK para {user_id}")
    return ok_count


def postular_empleos_lab(user_id: str, user: dict, empleos: list, max_n: int = 10) -> int:
    """
    Postula a los empleos dados usando la sesión guardada.
    Retorna el número de postulaciones exitosas.
    """
    if not empleos:
        return 0

    cuenta = bq.get_portal_account(user_id, PORTAL_ID)
    if not cuenta:
        print(f"  [lab-post] Sin cuenta para {user_id}")
        return 0

    email    = cuenta.get("email", "")
    password = cuenta.get("password", "")

    try:
        from playwright.sync_api import sync_playwright
        from portal_accounts import _make_pw_context, _new_stealth_page
    except ImportError:
        print("  [lab-post] Playwright no disponible")
        return 0

    ok_count = 0

    try:
        _, browser, ctx, _ = _make_pw_context()
        page = _new_stealth_page(ctx)
        try:

            # Inyectar cookies
            cookies = bq.get_portal_cookies(user_id, PORTAL_ID)
            if cookies:
                ctx.add_cookies([c for c in cookies if isinstance(c, dict) and "name" in c and "value" in c])
                print(f"  [lab-post] {len(cookies)} cookies inyectadas")

            # Verificar sesión
            page.goto(f"{BASE_URL}/mi-cuenta", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            if not _esta_logueado(page):
                if not _login(page, email, password):
                    return 0

            print(f"  [lab-post] Sesión activa — {min(len(empleos), max_n)} empleos a postular")

            ya_postulados = bq.get_applied_job_ids(user_id, days=60)

            salario = int(user.get("PRETENSION_GENERAL") or _SUELDO_DEFAULT)

            for emp in empleos[:max_n]:
                if ok_count >= max_n:
                    break
                url = emp.get("link", "")
                if not url or url in ya_postulados:
                    continue

                ok, motivo = _postular_uno(page, emp, salario, email=email)

                if motivo == "ya_postulado_previamente":
                    ya_postulados.add(url)
                    print(f"    ~ [lab] ya postulado antes: {emp.get('titulo','')[:50]}")
                else:
                    if ok:
                        bq.save_jobs([{
                            "id_empleo":         url,
                            "id_usuario":        user_id,
                            "titulo_empleo":     emp.get("titulo", ""),
                            "cargo":             emp.get("cargo", ""),
                            "Fecha_Postulacion": datetime.datetime.utcnow().isoformat(),
                            "empresa":           emp.get("empresa", ""),
                            "descripcion":       emp.get("descripcion", ""),
                            "link":              url,
                            "portal":            PORTAL_ID,
                        }])
                        ok_count += 1
                        ya_postulados.add(url)
                        print(f"    [lab] postulado: {emp.get('titulo','')[:50]}")
                    else:
                        print(f"    [lab] falló ({motivo}): {emp.get('titulo','')[:50]}")

                time.sleep(random.uniform(3, 6))

            bq.save_portal_cookies(user_id, PORTAL_ID, ctx.cookies())
        finally:
            try:
                browser.close()
            except Exception:
                pass

    except Exception as e:
        import traceback
        print(f"  [lab-post] Error: {e}")
        traceback.print_exc()

    print(f"  [lab-post] {ok_count} postulaciones OK para {user_id}")
    return ok_count
