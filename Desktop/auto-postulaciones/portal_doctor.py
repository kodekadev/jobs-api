"""
portal_doctor.py — Self-healing scraper health check.

Captura el HTML en vivo de una página del portal, lee el código de automatización
actual, y le pide a Claude que detecte selectores rotos y genere correcciones.
Cuando el desarrollador cambia el HTML del portal, este script lo detecta
automáticamente y propone los fixes necesarios.

Uso:
    python portal_doctor.py --portal chiletrabajos --page login
    python portal_doctor.py --portal trabajando   --page login
    python portal_doctor.py --portal chiletrabajos --page postular --url https://...
    python portal_doctor.py --portal chiletrabajos --page login --apply
"""
import os
import sys
import json
import re
import time
import argparse
from datetime import datetime
from pathlib import Path

import anthropic
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ── paths ────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent
_in_cloud_run = bool(os.environ.get("K_SERVICE"))
HEADLESS = os.environ.get("PLAYWRIGHT_HEADLESS", "1" if _in_cloud_run else "0") != "0"

# ── mapa portal → página → recursos ─────────────────────────────────────────
# Cada entrada define: la URL de la página, los archivos de módulo a analizar,
# y un selector CSS opcional para esperar antes de capturar el DOM.
PORTALS: dict[str, dict[str, dict]] = {
    "chiletrabajos": {
        "login": {
            "url": "https://www.chiletrabajos.cl/chtlogin",
            "files": ["chiletrabajos/login.py"],
            "wait_for": "#username",
        },
        "crear_cuenta": {
            "url": "https://www.chiletrabajos.cl/postulante/registro",
            "files": ["chiletrabajos/crear_cuenta.py"],
            "wait_for": None,
        },
        "completar_perfil": {
            "url": None,  # requiere sesión — pasar --url con cookie activa
            "files": ["chiletrabajos/completar_perfil.py"],
            "wait_for": None,
        },
        "postular": {
            "url": None,  # requiere URL de aviso — pasar con --url
            "files": ["chiletrabajos/postular.py"],
            "wait_for": None,
        },
    },
    "trabajando": {
        "login": {
            "url": "https://www.trabajando.cl/cuenta/login",
            "files": ["trabajando/login.py"],
            "wait_for": None,
        },
        "crear_cuenta": {
            "url": "https://www.trabajando.cl/cuenta/registro",
            "files": ["trabajando/crear_cuenta.py"],
            "wait_for": None,
        },
        "completar_perfil": {
            "url": None,
            "files": ["trabajando/completar_perfil.py"],
            "wait_for": None,
        },
        "postular": {
            "url": None,  # requiere URL de aviso — pasar con --url
            "files": ["trabajando/postular.py"],
            "wait_for": None,
        },
    },
    "indeed": {
        "postular": {
            "url": None,  # requiere URL de aviso — pasar con --url
            "files": ["indeed/postular.py", "indeed/login.py"],
            "wait_for": None,
        },
    },
}

# ── schema JSON que Claude debe respetar en su respuesta ─────────────────────
_JSON_SCHEMA_DOC = """
{
  "status": "ok" | "changes_detected",
  "analysis": "resumen breve de lo que encontraste en la página",
  "broken_selectors": [
    {
      "file": "ruta relativa del archivo Python",
      "function": "nombre de la función afectada",
      "old_selector": "selector / id / xpath exacto que ya no funciona",
      "old_code_line": "línea de código original (copia exacta)",
      "new_selector": "selector correcto según el HTML actual",
      "suggested_fix": "línea de código corregida (lista para pegar)",
      "reason": "por qué el selector viejo ya no funciona"
    }
  ]
}
"""


# ── captura del HTML ──────────────────────────────────────────────────────────

def capture_html(url: str, wait_selector: str | None = None) -> str:
    """
    Navega a la URL con Playwright (modo stealth) y devuelve el HTML completo.
    Espera que el DOM esté estabilizado antes de capturar.
    """
    print(f"  📡 Capturando HTML de {url} …")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
        )
        page = ctx.new_page()

        # Ocultar webdriver flag
        page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=10_000)
                except Exception:
                    pass
            else:
                time.sleep(3)  # espera genérica para páginas con JS pesado
            html = page.content()
        finally:
            browser.close()

    return html


# ── simplificación del HTML ───────────────────────────────────────────────────

# Atributos que importan para los selectores de automatización
_KEEP_ATTRS = {
    "id", "class", "name", "type", "placeholder", "href", "action",
    "method", "for", "value", "role", "aria-label", "data-testid",
    "autocomplete", "required",
}

# Tags que no aportan nada a un análisis de selectores
_DISCARD_TAGS = {
    "script", "style", "noscript", "svg", "path", "circle", "rect",
    "polygon", "meta", "link", "head", "iframe", "img", "video",
    "audio", "source", "track", "map", "area",
}


def simplify_html(raw_html: str, max_chars: int = 60_000) -> str:
    """
    Reduce el HTML a estructura útil para análisis de selectores.

    Elimina scripts, estilos, SVG y atributos de presentación.
    Conserva ids, clases, atributos de formularios y texto visible.
    Trunca a max_chars para no saturar el contexto de Claude.
    """
    soup = BeautifulSoup(raw_html, "html.parser")

    # Eliminar tags de ruido
    for tag in soup(_DISCARD_TAGS):
        tag.decompose()

    for tag in soup.find_all(True):
        # Limpiar atributos no relevantes
        for attr in [k for k in list(tag.attrs) if k not in _KEEP_ATTRS]:
            del tag[attr]

        # Limitar número de clases (las primeras 6 suelen ser suficientes)
        if tag.get("class"):
            tag["class"] = tag["class"][:6]

        # Truncar valores largos
        for attr in ("value", "placeholder", "aria-label"):
            if tag.get(attr) and len(str(tag[attr])) > 80:
                tag[attr] = str(tag[attr])[:80] + "…"

        # Truncar texto directo del nodo
        if tag.string and len(tag.string.strip()) > 120:
            tag.string = tag.string.strip()[:120] + "…"

    result = str(soup)
    result = re.sub(r"\s{2,}", " ", result)  # colapsar whitespace

    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n<!-- [HTML truncado por longitud] -->"

    return result.strip()


# ── lectura de archivos del módulo ────────────────────────────────────────────

def read_module_files(file_paths: list[str]) -> str:
    """Lee y concatena los archivos Python del módulo relevante."""
    parts = []
    for rel_path in file_paths:
        path = _ROOT / rel_path
        if path.exists():
            parts.append(f"# {'='*60}\n# {rel_path}\n# {'='*60}\n{path.read_text(encoding='utf-8')}")
        else:
            parts.append(f"# [ARCHIVO NO ENCONTRADO: {rel_path}]")
    return "\n\n".join(parts)


# ── llamada a Claude ──────────────────────────────────────────────────────────

def ask_claude(
    portal: str,
    page: str,
    html_tree: str,
    module_code: str,
    url: str,
) -> dict:
    """
    Envía el HTML simplificado + el código del módulo a Claude.

    Claude analiza si los selectores del código (CSS ids, clases, XPath,
    atributos de formulario) siguen presentes en el HTML actual.
    Devuelve un dict con el diagnóstico estructurado.
    """
    client = anthropic.Anthropic()

    system_prompt = (
        "Eres un experto en mantenimiento de web scrapers Python (Selenium y Playwright). "
        "Tu tarea es comparar código de automatización con el HTML actual de un portal de empleo "
        "y detectar qué selectores CSS/XPath/ID ya no funcionan porque el desarrollador cambió el HTML.\n\n"
        "Reglas estrictas:\n"
        "1. Extrae TODOS los selectores del código: IDs, clases CSS, XPath, nombres de campo, "
        "atributos name/placeholder/value usados para localizar elementos.\n"
        "2. Verifica cada uno contra el HTML proporcionado.\n"
        "3. SOLO reporta los que YA NO EXISTEN en el HTML actual. "
        "Si un selector sigue funcionando, no lo menciones.\n"
        "4. Para cada selector roto, encuentra el elemento equivalente en el HTML nuevo "
        "y propone el selector correcto.\n"
        "5. Tu respuesta debe ser ÚNICAMENTE JSON válido, sin texto adicional, "
        "siguiendo exactamente este schema:\n"
        f"{_JSON_SCHEMA_DOC}"
    )

    user_message = (
        f"Portal: **{portal}**\n"
        f"Página: **{page}**\n"
        f"URL capturada: {url}\n"
        f"Fecha/hora: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        "## Código de automatización actual (Python):\n\n"
        f"```python\n{module_code}\n```\n\n"
        "## HTML en vivo del portal (simplificado):\n\n"
        f"```html\n{html_tree}\n```\n\n"
        "Analiza el código y el HTML. "
        "¿Qué selectores del código ya no coinciden con el HTML actual? "
        "Propón los fixes necesarios en formato JSON."
    )

    print("  🤖 Consultando a Claude …")
    with client.messages.stream(
        model="claude-opus-4-8",
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        message = stream.get_final_message()

    # Extraer el JSON de la respuesta
    for block in message.content:
        if hasattr(block, "type") and block.type == "text":
            text = block.text.strip()
            # Limpiar posibles backticks de markdown
            text = re.sub(r"^```json\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                print(f"  ⚠️  Respuesta no es JSON válido: {e}")
                print(f"  Raw: {text[:200]}")
                return {
                    "status": "error",
                    "analysis": f"Error al parsear respuesta: {e}",
                    "broken_selectors": [],
                    "raw_response": text,
                }

    return {
        "status": "error",
        "analysis": "Claude no retornó texto",
        "broken_selectors": [],
    }


# ── aplicar fixes automáticamente ────────────────────────────────────────────

def apply_fixes(result: dict) -> int:
    """
    Aplica los fixes sugeridos directamente en los archivos Python.

    Reemplaza la línea vieja por la línea corregida en cada archivo afectado.
    Retorna el número de fixes aplicados.
    """
    applied = 0
    for fix in result.get("broken_selectors", []):
        old_line = fix.get("old_code_line", "").strip()
        new_line = fix.get("suggested_fix", "").strip()
        file_path = _ROOT / fix.get("file", "")

        if not old_line or not new_line or not file_path.exists():
            continue

        content = file_path.read_text(encoding="utf-8")
        if old_line in content:
            # Preservar la indentación original
            match = re.search(r"^(\s*)" + re.escape(old_line), content, re.MULTILINE)
            if match:
                indent = match.group(1)
                new_content = content.replace(
                    indent + old_line,
                    indent + new_line,
                    1,
                )
                file_path.write_text(new_content, encoding="utf-8")
                print(f"  ✅ Fix aplicado en {fix['file']}:{fix['function']}")
                applied += 1
        else:
            print(f"  ⚠️  Línea no encontrada en {fix['file']} — fix manual necesario")

    return applied


# ── reporte en consola ────────────────────────────────────────────────────────

def print_report(result: dict, portal: str, page: str) -> None:
    """Imprime el diagnóstico en consola con formato legible."""
    status = result.get("status", "?")
    is_ok = status == "ok"

    print(f"\n{'='*60}")
    print(f"  {'' if is_ok else '⚠️ '}  {portal}/{page}  →  {status.upper()}")
    print(f"{'='*60}")
    print(f"  {result.get('analysis', '')}")

    broken = result.get("broken_selectors", [])
    if not broken:
        print("\n  No se detectaron selectores rotos. El módulo está actualizado.")
    else:
        print(f"\n  Selectores rotos detectados: {len(broken)}\n")
        for i, fix in enumerate(broken, 1):
            print(f"  [{i}] {fix.get('file', '?')}  →  def {fix.get('function', '?')}()")
            print(f"       ❌  Viejo  :  {fix.get('old_selector', '?')}")
            print(f"       ✅  Nuevo  :  {fix.get('new_selector', '?')}")
            print(f"       💡  Razón  :  {fix.get('reason', '?')}")
            if fix.get("suggested_fix"):
                print(f"       🔧  Fix    :  {fix['suggested_fix']}")
            print()


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Portal Doctor — detecta selectores rotos con Claude y propone fixes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python portal_doctor.py --portal chiletrabajos --page login\n"
            "  python portal_doctor.py --portal trabajando --page login\n"
            "  python portal_doctor.py --portal chiletrabajos --page postular \\\n"
            "      --url https://www.chiletrabajos.cl/empleo/12345\n"
            "  python portal_doctor.py --portal chiletrabajos --page login --apply\n"
        ),
    )
    parser.add_argument(
        "--portal",
        required=True,
        choices=list(PORTALS.keys()),
        help="Portal a revisar",
    )
    parser.add_argument(
        "--page",
        required=True,
        help="Página a revisar (login, postular, completar_perfil, crear_cuenta)",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="URL de la página (override; requerido para páginas que necesitan sesión o URL de aviso)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Aplicar los fixes automáticamente en los archivos Python",
    )
    args = parser.parse_args()

    # ── validaciones ──────────────────────────────────────────────────────────
    portal_config = PORTALS.get(args.portal, {})
    page_config = portal_config.get(args.page)
    if not page_config:
        valid = ", ".join(portal_config.keys()) if portal_config else "(portal no registrado)"
        print(f"❌ Página '{args.page}' no configurada para '{args.portal}'.")
        print(f"   Opciones válidas: {valid}")
        sys.exit(1)

    url = args.url or page_config.get("url")
    if not url:
        print(
            f"❌ '{args.page}' requiere --url (necesita URL de aviso de empleo o sesión autenticada).\n"
            f"   Ejemplo: python portal_doctor.py --portal {args.portal} --page {args.page} \\\n"
            f"             --url https://www.{args.portal}.cl/empleo/12345"
        )
        sys.exit(1)

    print(f"\n🩺  Portal Doctor — {args.portal}/{args.page}")
    print(f"   URL       : {url}")
    print(f"   Headless  : {HEADLESS}")
    print(f"   Auto-apply: {args.apply}\n")

    # ── paso 1: capturar HTML ─────────────────────────────────────────────────
    raw_html = capture_html(url, wait_selector=page_config.get("wait_for"))

    # ── paso 2: simplificar HTML ──────────────────────────────────────────────
    print("  🧹 Simplificando HTML …")
    html_tree = simplify_html(raw_html)
    print(f"     {len(raw_html):,} chars  →  {len(html_tree):,} chars simplificados")

    # ── paso 3: leer código del módulo ────────────────────────────────────────
    module_code = read_module_files(page_config["files"])
    print(f"  📄 Módulos leídos: {len(page_config['files'])} archivo(s), {len(module_code):,} chars")

    # ── paso 4: consultar a Claude ────────────────────────────────────────────
    result = ask_claude(
        portal=args.portal,
        page=args.page,
        html_tree=html_tree,
        module_code=module_code,
        url=url,
    )

    # ── paso 5: mostrar resultado ─────────────────────────────────────────────
    print_report(result, args.portal, args.page)

    # ── paso 6: aplicar fixes (opcional) ─────────────────────────────────────
    if args.apply and result.get("broken_selectors"):
        print("\n  🔧 Aplicando fixes automáticamente …")
        n = apply_fixes(result)
        print(f"     {n} fix(es) aplicado(s)")
    elif args.apply and not result.get("broken_selectors"):
        print("  No hay fixes que aplicar.")

    # ── paso 7: guardar reporte JSON ──────────────────────────────────────────
    report = {
        "portal": args.portal,
        "page": args.page,
        "url": url,
        "checked_at": datetime.now().isoformat(),
        **result,
    }
    out_dir = _ROOT / args.portal
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.page}.doctor.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  💾 Reporte guardado en: {out_path.relative_to(_ROOT)}\n")


if __name__ == "__main__":
    main()
