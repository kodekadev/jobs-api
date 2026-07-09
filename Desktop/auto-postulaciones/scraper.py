"""
Scraper multi-portal — sin Selenium, sin login.

Portales soportados:
  - LinkedIn  (via jobspy)
  - Indeed    (via jobspy)
  - Trabajando.cl (HTTP directo)
  - ChileTrabajos.cl (HTTP directo)
  - Computrabajo.cl (HTTP directo)
"""

import json as _json
import re
import time
import random
import urllib.parse
import urllib.request
import urllib.error

try:
    from jobspy import scrape_jobs
    JOBSPY_OK = True
except ImportError:
    JOBSPY_OK = False
    print("jobspy no disponible - se usaran solo portales chilenos")

# ---------------------------------------------------------------------------
# Headers comunes para HTTP scraping
# ---------------------------------------------------------------------------
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-CL,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _get(url: str, timeout: int = 15, retries: int = 2) -> str:
    """GET con reintentos, retorna HTML o '' si falla."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                wait = (attempt + 1) * 5
                print(f"  ⚠ GET {url[:60]} → HTTP {e.code}, esperando {wait}s...")
                time.sleep(wait)
            else:
                print(f"  ⚠ GET {url[:60]}: HTTP {e.code}")
                break
        except Exception as e:
            if attempt < retries:
                time.sleep(3)
            else:
                print(f"  ⚠ GET {url[:60]}: {e}")
    return ""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# jobspy — LinkedIn + Indeed
# ---------------------------------------------------------------------------
def _scrape_jobspy(cargo: str, ubicacion: str, n: int) -> list[dict]:
    if not JOBSPY_OK:
        return []
    jobs = []
    try:
        df = scrape_jobs(
            site_name=["linkedin"],
            search_term=cargo,
            location=f"{ubicacion}, Chile",
            results_wanted=n,
            linkedin_fetch_description=True,
            hours_old=48,
        )
        if df is not None and not df.empty:
            df = df.fillna("")
            for _, row in df.iterrows():
                jobs.append({
                    "id":          str(row.get("id", "")).strip(),
                    "titulo":      str(row.get("title", "")).strip(),
                    "empresa":     str(row.get("company", "")).strip(),
                    "descripcion": str(row.get("description", "")).strip(),
                    "link":        str(row.get("job_url", "")).strip(),
                    "direct_url":  str(row.get("job_url_direct", "")).strip(),
                    "ubicacion":   str(row.get("location", "")).strip(),
                    "fuente":      str(row.get("site", "linkedin")).strip(),
                })
    except Exception as e:
        print(f"  ⚠ jobspy '{cargo}' en '{ubicacion}': {e}")
    return jobs


# ---------------------------------------------------------------------------
# Trabajando.cl — Playwright (SPA Vue, fetch API con timeout)
# ---------------------------------------------------------------------------
def _scrape_trabajando_playwright(page, cargo: str, ubicacion: str, n: int) -> list[dict]:
    """
    Scraper de Trabajando.cl usando Playwright page ya autenticada.
    Navega a la página, descubre el endpoint de la API via performance entries,
    luego llama a ese endpoint paginado con fetch() (con AbortController para timeout).
    NO usa page.on('response') para evitar bloqueos del event loop.
    """
    import sys
    jobs: list[dict] = []
    cargo_enc     = urllib.parse.quote(cargo)
    ubicacion_enc = urllib.parse.quote(ubicacion)
    nav_url = f"https://www.trabajando.cl/trabajo-empleo/{cargo_enc}?ubicacion={ubicacion_enc}"

    try:
        page.goto(nav_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3500)  # dejar que Vue dispare las API calls

        # Descubrir endpoint de búsqueda (no-bloqueante, corre JS)
        api_urls: list[str] = page.evaluate("""
            () => {
                try {
                    return window.performance.getEntriesByType('resource')
                        .filter(function(e) {
                            return (e.initiatorType === 'fetch' || e.initiatorType === 'xmlhttprequest')
                                && (e.name.indexOf('api.trabajando') !== -1 || e.name.indexOf('trabajando.cl/api') !== -1)
                                && e.name.indexOf('candidato') === -1
                                && e.name.indexOf('postulacion') === -1
                                && e.name.indexOf('estadistica') === -1
                                && e.name.indexOf('configportal') === -1
                                && e.name.indexOf('login') === -1;
                        })
                        .map(function(e) { return e.name; });
                } catch(ex) { return []; }
            }
        """) or []

        search_base: str | None = None
        for u in api_urls:
            if any(kw in u for kw in ("oferta", "busqueda", "search", "empleo", "cargo")):
                search_base = u
                break
        if not search_base and api_urls:
            search_base = api_urls[0]

        if not search_base:
            print(f"    [tbj-pw] Sin endpoint API para '{cargo}' ({len(api_urls)} resources)", flush=True)
            return []

        # Paginar con fetch+AbortController (timeout 10s por página)
        for pagina in range(1, 4):
            if len(jobs) >= n:
                break
            page_url = re.sub(r"pagina=\d+", f"pagina={pagina}", search_base)
            if "pagina=" not in page_url:
                page_url += ("&" if "?" in page_url else "?") + f"pagina={pagina}"
            page_url = re.sub(r"cantidadResultados=\d+", f"cantidadResultados={min(n, 50)}", page_url)

            try:
                result = page.evaluate("""
                    async (url) => {
                        var ctrl = new AbortController();
                        var timer = setTimeout(function() { ctrl.abort(); }, 10000);
                        try {
                            var r = await fetch(url, {credentials: 'include', signal: ctrl.signal});
                            clearTimeout(timer);
                            if (!r.ok) return {ok: false, status: r.status};
                            return {ok: true, data: await r.json()};
                        } catch(e) {
                            clearTimeout(timer);
                            return {ok: false, error: String(e)};
                        }
                    }
                """, page_url)
            except Exception as e:
                print(f"    [tbj-pw] evaluate error p{pagina}: {e}", flush=True)
                break

            if not result or not result.get("ok"):
                err = result.get("error", result.get("status", "?")) if result else "null"
                print(f"    [tbj-pw] fetch fail p{pagina}: {err}", flush=True)
                break

            data = result.get("data") or {}
            ofertas = (
                data.get("ofertas") or
                (data.get("data") or {}).get("ofertas") or
                data.get("results") or data.get("items") or []
            )
            if not ofertas:
                break

            for o in ofertas:
                id_o   = o.get("idOferta") or o.get("id") or o.get("ofertaId")
                titulo = o.get("nombreCargo") or o.get("titulo") or o.get("cargo") or ""
                empr   = o.get("nombreEmpresa") or o.get("empresa") or ""
                url_o  = o.get("urlFicha") or o.get("url") or ""
                if not (id_o and titulo):
                    continue
                if url_o and url_o.startswith("http"):
                    link = url_o
                elif url_o:
                    link = f"https://www.trabajando.cl{url_o}"
                else:
                    link = f"https://www.trabajando.cl/trabajo-empleo/{cargo_enc}/trabajo/{id_o}-j"
                jobs.append({
                    "id": link, "titulo": titulo, "empresa": empr,
                    "descripcion": "", "link": link,
                    "ubicacion": ubicacion, "fuente": "trabajando",
                })

            time.sleep(0.5)

        print(f"    [tbj-pw] {len(jobs)} empleos para '{cargo}' en '{ubicacion}'", flush=True)

    except Exception as e:
        print(f"    [tbj-pw] Error: {e}", flush=True)

    return jobs[:n]


# ---------------------------------------------------------------------------
# Trabajando.cl — Selenium (SPA Vue renderiza empleos en el cliente)
# ---------------------------------------------------------------------------
def _scrape_trabajando(cargo: str, ubicacion: str, n: int, driver=None) -> list[dict]:
    """
    Trabajando.cl es una SPA Vue — el HTML estático no contiene empleos.
    Requiere un driver de Selenium ya logueado para renderizar los resultados.

    Estrategia:
      1. Leer respuestas XHR capturadas (window._net_responses) → datos API directos
      2. Fallback: DOM scraping con scroll para infinite scroll
    """
    if driver is None:
        return []

    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    jobs = []
    cargo_enc     = urllib.parse.quote(cargo)
    ubicacion_enc = urllib.parse.quote(ubicacion)
    url = f"https://www.trabajando.cl/trabajo-empleo/{cargo_enc}?ubicacion={ubicacion_enc}"

    try:
        # Limpiar respuestas previas para no mezclar con login
        try:
            driver.execute_script("window._net_responses = []")
        except Exception:
            pass

        driver.get(url)
        time.sleep(5)  # esperar renderizado Vue inicial

        # ── 1. API via fetch() desde el contexto del browser ──────────────────
        # Descubrir endpoint real con performance.getEntries(), luego paginamos
        try:
            # Descubrir qué URLs de API llamó la página de búsqueda
            api_urls = driver.execute_script("""
                return window.performance.getEntriesByType('resource')
                    .filter(function(e) {
                        return (e.initiatorType === 'fetch' || e.initiatorType === 'xmlhttprequest')
                            && (e.name.indexOf('api.trabajando') !== -1 || e.name.indexOf('trabajando.cl/api') !== -1)
                            && e.name.indexOf('candidato') === -1
                            && e.name.indexOf('postulacion') === -1
                            && e.name.indexOf('estadistica') === -1
                            && e.name.indexOf('configportal') === -1;
                    })
                    .map(function(e) { return e.name; });
            """) or []

            # Elegir la primera URL candidata como plantilla para paginación
            search_url_base = None
            for u in api_urls:
                # Buscar endpoint con parámetros que indiquen búsqueda de empleos
                if any(kw in u for kw in ["oferta", "busqueda", "search", "empleo", "cargo"]):
                    search_url_base = u
                    break
            # Fallback: si no encontramos por keywords, usar la primera URL API
            if not search_url_base and api_urls:
                search_url_base = api_urls[0]
                print(f"    [tbj-api] endpoints capturados: {[u[:70] for u in api_urls[:3]]}")

            if search_url_base:
                # Llamar al endpoint con paginación para obtener más resultados
                api_jobs: list[dict] = []
                for pagina in range(1, 4):  # páginas 1, 2, 3
                    if len(api_jobs) >= n:
                        break
                    # Reemplazar o añadir parámetros de paginación
                    page_url = re.sub(r"pagina=\d+", f"pagina={pagina}", search_url_base)
                    if "pagina=" not in page_url:
                        sep = "&" if "?" in page_url else "?"
                        page_url += f"{sep}pagina={pagina}"
                    # Aumentar cantidadResultados si aparece en la URL
                    page_url = re.sub(r"cantidadResultados=\d+", f"cantidadResultados={n}", page_url)

                    try:
                        result = driver.execute_async_script("""
                            var done = arguments[0];
                            var url = arguments[1];
                            fetch(url, {credentials: 'include'})
                                .then(function(r) { return r.text(); })
                                .then(function(t) { done({ok: true, text: t}); })
                                .catch(function(e) { done({ok: false, err: String(e)}); });
                        """, page_url)

                        if not result or not result.get("ok"):
                            break
                        raw = result.get("text", "") or ""
                        data = _json.loads(raw)
                        ofertas = (
                            data.get("ofertas") or
                            (data.get("data") or {}).get("ofertas") or
                            data.get("results") or
                            data.get("items") or
                            []
                        )
                        if not ofertas:
                            break
                        for o in ofertas:
                            id_o   = o.get("idOferta") or o.get("id") or o.get("ofertaId")
                            titulo = o.get("nombreCargo") or o.get("titulo") or o.get("cargo") or ""
                            empr   = o.get("nombreEmpresa") or o.get("empresa") or ""
                            url_o  = o.get("urlFicha") or o.get("url") or ""
                            if not (id_o and titulo):
                                continue
                            if url_o and url_o.startswith("http"):
                                link = url_o
                            elif url_o:
                                link = f"https://www.trabajando.cl{url_o}"
                            else:
                                link = f"https://www.trabajando.cl/trabajo-empleo/{cargo_enc}/trabajo/{id_o}-j"
                            api_jobs.append({
                                "id": link, "titulo": titulo, "empresa": empr,
                                "descripcion": "", "link": link,
                                "ubicacion": ubicacion, "fuente": "trabajando",
                            })
                    except Exception:
                        break

                if api_jobs:
                    print(f"    [tbj-api] {len(api_jobs)} empleos via API ({len(api_urls)} endpoints)")
                    return api_jobs[:n]
            else:
                print(f"    [tbj-api] sin endpoints — {len(api_urls)} resources capturados")
        except Exception:
            pass

        # ── 2. Fallback: DOM scraping multi-página ────────────────────────────
        def _extract_rows_from_dom() -> list[dict]:
            try:
                tabla2 = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.ID, "listadoOfertas"))
                )
            except Exception:
                return []
            found = []
            for row in tabla2.find_elements(By.CLASS_NAME, "result-box"):
                try:
                    try:
                        a = row.find_element(By.CSS_SELECTOR, "h2 a, h3 a")
                    except Exception:
                        a = row.find_element(By.CSS_SELECTOR, "a[href]")
                    link_r   = a.get_attribute("href") or ""
                    titulo_r = _clean(a.text)
                    try:
                        empresa_r = _clean(row.find_element(By.CSS_SELECTOR, ".empresa, .company, [class*='empresa']").text)
                    except Exception:
                        empresa_r = ""
                    if titulo_r and link_r:
                        found.append({
                            "id": link_r, "titulo": titulo_r, "empresa": empresa_r,
                            "descripcion": "", "link": link_r,
                            "ubicacion": ubicacion, "fuente": "trabajando",
                        })
                except Exception:
                    continue
            return found

        seen_links: set = set()
        # Page 1 is already loaded in the browser — just extract
        for j in _extract_rows_from_dom():
            if j["link"] not in seen_links:
                seen_links.add(j["link"])
                jobs.append(j)

        # Pages 2+ — navigate then extract
        for pagina in range(2, 4):
            if len(jobs) >= n:
                break
            driver.get(f"{url}&pagina={pagina}")
            time.sleep(3)
            page_jobs = _extract_rows_from_dom()
            if not page_jobs:
                break
            new_count = 0
            for j in page_jobs:
                if j["link"] not in seen_links:
                    seen_links.add(j["link"])
                    jobs.append(j)
                    new_count += 1
            if new_count == 0:
                break

    except Exception as e:
        print(f"  [trabajando] Selenium error en scraping: {e}")

    return jobs


# ---------------------------------------------------------------------------
# ChileTrabajos.cl — HTTP directo
# ---------------------------------------------------------------------------
def _scrape_chiletrabajos_selenium(driver, cargo: str, ubicacion: str, n: int) -> list[dict]:
    """Scraper de ChileTrabajos usando el driver Selenium logueado (carga JS)."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    # Param name es "2" (no "trabajo") + hidden "f=2"
    cargo_enc = urllib.parse.quote_plus(cargo)
    url = f"https://www.chiletrabajos.cl/encuentra-un-empleo?2={cargo_enc}&f=2"
    driver.get(url)

    # Esperar que los resultados carguen (job-item con link /trabajo/)
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".job-item a[href*='/trabajo/']"))
        )
    except Exception:
        time.sleep(3)

    html = driver.page_source
    jobs = []

    # Extraer bloques job-item del HTML renderizado
    blocks = re.findall(
        r'<div[^>]+class="[^"]*job[_-]?item[^"]*"[^>]*>(.*?)</div>\s*</div>',
        html, re.DOTALL
    )

    for block in blocks:
        if len(jobs) >= n:
            break
        # Link principal al empleo (/trabajo/slug-NNNNN)
        link_m = re.search(r'href="(https://www\.chiletrabajos\.cl/trabajo/[^"]+)"', block)
        if not link_m:
            continue
        link = link_m.group(1)

        # Título: buscar <a> que apunte al mismo link
        titulo_m = re.search(
            r'href="' + re.escape(link) + r'"[^>]*>([^<]{3,100})',
            block
        )
        if not titulo_m:
            # Fallback: <strong> o primer <h>
            titulo_m = re.search(r'<(?:strong|h[2-4])[^>]*>([^<]{3,100})</(?:strong|h[2-4])>', block)
        titulo = _clean(titulo_m.group(1)) if titulo_m else ""
        if not titulo or titulo.lower() in ("ver más", "ver mas", "postular"):
            continue

        # Empresa: span con clase empresa (no el link de ciudad)
        emp_m = (
            re.search(r'<span[^>]+class="[^"]*empresa[^"]*"[^>]*>([^<]+)', block)
            or re.search(r'class="[^"]*company[^"]*"[^>]*>([^<]{3,60})', block)
        )
        empresa = _clean(emp_m.group(1)) if emp_m else ""

        # Descripción: párrafo con texto de la tarjeta
        desc_m = re.search(r'<p[^>]*style="[^"]*break-all[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL)
        if not desc_m:
            desc_m = re.search(r'<p[^>]*class="[^"]*descripcion[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL)
        descripcion = ""
        if desc_m:
            raw = re.sub(r'<[^>]+>', ' ', desc_m.group(1))
            descripcion = _clean(raw)[:5000]

        jobs.append({
            "id":          link,
            "titulo":      titulo,
            "empresa":     empresa,
            "descripcion": descripcion,
            "link":        link,
            "ubicacion":   ubicacion,
            "fuente":      "chiletrabajos",
        })

    return jobs


def _scrape_chiletrabajos_pw(page, cargo: str, ubicacion: str, n: int) -> list[dict]:
    """Scraper de ChileTrabajos usando Playwright Page autenticada."""
    cargo_enc = urllib.parse.quote_plus(cargo)
    url = f"https://www.chiletrabajos.cl/encuentra-un-empleo?2={cargo_enc}&f=2"
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    try:
        page.wait_for_selector(".job-item a[href*='/trabajo/']", timeout=10000)
    except Exception:
        page.wait_for_timeout(3000)

    html = page.content()
    jobs = []

    blocks = re.findall(
        r'<div[^>]+class="[^"]*job[_-]?item[^"]*"[^>]*>(.*?)</div>\s*</div>',
        html, re.DOTALL
    )
    for block in blocks:
        if len(jobs) >= n:
            break
        link_m = re.search(r'href="(https://www\.chiletrabajos\.cl/trabajo/[^"]+)"', block)
        if not link_m:
            continue
        link = link_m.group(1)
        titulo_m = re.search(r'href="' + re.escape(link) + r'"[^>]*>([^<]{3,100})', block)
        if not titulo_m:
            titulo_m = re.search(r'<(?:strong|h[2-4])[^>]*>([^<]{3,100})</(?:strong|h[2-4])>', block)
        titulo = _clean(titulo_m.group(1)) if titulo_m else ""
        if not titulo or titulo.lower() in ("ver más", "ver mas", "postular"):
            continue
        emp_m = (
            re.search(r'<span[^>]+class="[^"]*empresa[^"]*"[^>]*>([^<]+)', block)
            or re.search(r'class="[^"]*company[^"]*"[^>]*>([^<]{3,60})', block)
        )
        empresa = _clean(emp_m.group(1)) if emp_m else ""
        desc_m = re.search(r'<p[^>]*style="[^"]*break-all[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL)
        if not desc_m:
            desc_m = re.search(r'<p[^>]*class="[^"]*descripcion[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL)
        descripcion = ""
        if desc_m:
            raw = re.sub(r'<[^>]+>', ' ', desc_m.group(1))
            descripcion = _clean(raw)[:5000]
        jobs.append({
            "id": link, "titulo": titulo, "empresa": empresa,
            "descripcion": descripcion, "link": link,
            "ubicacion": ubicacion, "fuente": "chiletrabajos",
        })
    return jobs


def _scrape_chiletrabajos(cargo: str, ubicacion: str, n: int, driver=None, page=None) -> list[dict]:
    """
    Scraper de ChileTrabajos. Usa Playwright page (preferido) o Selenium driver.
    Sin sesión, devuelve lista vacía (ChileTrabajos requiere JS).
    """
    if page is not None:
        return _scrape_chiletrabajos_pw(page, cargo, ubicacion, n)
    if driver is not None:
        return _scrape_chiletrabajos_selenium(driver, cargo, ubicacion, n)
    print("    [ChileTrabajos] Sin sesión — omitiendo")
    return []


# ---------------------------------------------------------------------------
# Computrabajo.cl — HTTP directo
# ---------------------------------------------------------------------------
def _scrape_computrabajo(cargo: str, ubicacion: str, n: int) -> list[dict]:
    """
    URL pública: https://www.computrabajo.cl/trabajo-de-{cargo}
    """
    jobs = []
    slug  = cargo.lower().replace(" ", "-")
    slug  = re.sub(r"[^a-z0-9\-]", "", slug)
    url   = f"https://www.computrabajo.cl/trabajo-de-{slug}"
    html  = _get(url)
    if not html:
        return jobs

    # Computrabajo usa article[data-id] o divs con clase "js-offer-link"
    # Buscar links a ofertas
    for m in re.finditer(
        r'href="(/oferta-de-trabajo/[^"]+)"[^>]*>[\s\S]{0,200}?<h2[^>]*>([^<]{5,100})</h2>',
        html
    ):
        if len(jobs) >= n:
            break
        link = f"https://www.computrabajo.cl{m.group(1)}"
        jobs.append({
            "id":          link,
            "titulo":      _clean(m.group(2)),
            "empresa":     "",
            "descripcion": "",
            "link":        link,
            "ubicacion":   ubicacion,
            "fuente":      "computrabajo",
        })

    return jobs


# ---------------------------------------------------------------------------
# Función principal — combina todos los portales
# ---------------------------------------------------------------------------
def find_jobs(cargo: str, ubicacion: str, n: int = 40, trabajando_driver=None,
              chiletrabajos_driver=None, trabajando_page=None,
              chiletrabajos_page=None) -> list[dict]:
    """
    Busca empleos en todos los portales configurados.
    trabajando_page:    Playwright Page autenticada (preferida).
    trabajando_driver:  Selenium driver (fallback).
    chiletrabajos_page: Playwright Page autenticada (preferida).
    chiletrabajos_driver: Selenium driver (fallback, sin uso en Cloud Run).
    Retorna lista de dicts con: id, titulo, empresa, descripcion, link, ubicacion, fuente.
    """
    all_jobs: list[dict] = []

    def _tbj():
        if trabajando_page is not None:
            return _scrape_trabajando_playwright(trabajando_page, cargo, ubicacion, n)
        return _scrape_trabajando(cargo, ubicacion, n, driver=trabajando_driver)

    scrapers = [
        ("Trabajando.cl", _tbj),
        ("ChileTrabajos",  lambda: _scrape_chiletrabajos(cargo, ubicacion, n, driver=chiletrabajos_driver, page=chiletrabajos_page)),
    ]

    for nombre, fn in scrapers:
        try:
            results = fn()
            print(f"    [{nombre}] {len(results)} empleos")
            all_jobs.extend(results)
        except Exception as e:
            print(f"    ⚠ [{nombre}] error: {e}")

        # Pausa entre portales para no saturar
        time.sleep(random.uniform(1.5, 3))

    # Filtrar resultados sin título o con título claramente irrelevante
    cargo_words = set(cargo.lower().split())
    filtered = []
    for j in all_jobs:
        titulo = j.get("titulo", "").lower()
        if not titulo:
            continue
        # Al menos una palabra del cargo debe aparecer en el título
        if any(w in titulo for w in cargo_words if len(w) > 3):
            filtered.append(j)

    discarded = len(all_jobs) - len(filtered)
    if discarded:
        print(f"    [filtro relevancia] descartados {discarded} empleos irrelevantes")

    return filtered
