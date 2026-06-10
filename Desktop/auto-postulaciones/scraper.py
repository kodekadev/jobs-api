"""
Scraper multi-portal — sin Selenium, sin login.

Portales soportados:
  - LinkedIn  (via jobspy)
  - Indeed    (via jobspy)
  - Trabajando.cl (HTTP directo)
  - ChileTrabajos.cl (HTTP directo)
  - Computrabajo.cl (HTTP directo)
"""

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
            site_name=["linkedin", "indeed"],
            search_term=cargo,
            location=f"{ubicacion}, Chile",
            results_wanted=n,
            country_indeed="Chile",
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
                    "ubicacion":   str(row.get("location", "")).strip(),
                    "fuente":      str(row.get("site", "linkedin")).strip(),
                })
    except Exception as e:
        print(f"  ⚠ jobspy '{cargo}' en '{ubicacion}': {e}")
    return jobs


# ---------------------------------------------------------------------------
# Trabajando.cl — Selenium (SPA Vue renderiza empleos en el cliente)
# ---------------------------------------------------------------------------
def _scrape_trabajando(cargo: str, ubicacion: str, n: int, driver=None) -> list[dict]:
    """
    Trabajando.cl es una SPA Vue — el HTML estático no contiene empleos.
    Requiere un driver de Selenium ya logueado para renderizar los resultados.
    Retorna lista vacía si no se provee driver.
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
        driver.get(url)
        time.sleep(5)  # esperar renderizado Vue

        wait = WebDriverWait(driver, 15)

        # Contenedor principal de resultados
        try:
            tabla = wait.until(EC.presence_of_element_located((By.ID, "listadoOfertas")))
            rows  = tabla.find_elements(By.CLASS_NAME, "result-box")
        except Exception:
            # fallback: buscar links directos a empleos
            rows = []
            links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/empleo/']")
            seen = set()
            for a in links[:n]:
                href  = a.get_attribute("href") or ""
                title = a.text.strip()
                if href and href not in seen and title:
                    seen.add(href)
                    jobs.append({
                        "id": href, "titulo": title, "empresa": "",
                        "descripcion": "", "link": href,
                        "ubicacion": ubicacion, "fuente": "trabajando",
                    })
            return jobs

        for row in rows[:n]:
            try:
                # Link y título desde h2/h3 o primer <a>
                try:
                    a = row.find_element(By.CSS_SELECTOR, "h2 a, h3 a")
                except Exception:
                    a = row.find_element(By.CSS_SELECTOR, "a[href]")
                link   = a.get_attribute("href") or ""
                titulo = _clean(a.text)

                # Empresa
                try:
                    empresa = _clean(row.find_element(By.CSS_SELECTOR, ".empresa, .company, [class*='empresa']").text)
                except Exception:
                    empresa = ""

                if titulo and link:
                    jobs.append({
                        "id":          link,
                        "titulo":      titulo,
                        "empresa":     empresa,
                        "descripcion": "",
                        "link":        link,
                        "ubicacion":   ubicacion,
                        "fuente":      "trabajando",
                    })
            except Exception:
                continue

    except Exception as e:
        print(f"  [trabajando] Selenium error en scraping: {e}")

    return jobs


# ---------------------------------------------------------------------------
# ChileTrabajos.cl — HTTP directo
# ---------------------------------------------------------------------------
def _scrape_chiletrabajos(cargo: str, ubicacion: str, n: int) -> list[dict]:
    """
    URL pública de búsqueda: https://www.chiletrabajos.cl/encuentra-un-empleo?trabajo={cargo}
    """
    jobs = []
    cargo_enc = urllib.parse.quote_plus(cargo)
    url = f"https://www.chiletrabajos.cl/encuentra-un-empleo?trabajo={cargo_enc}"
    html = _get(url)
    if not html:
        return jobs

    # Bloques de oferta
    blocks = re.findall(
        r'<div[^>]+class="[^"]*job[_-]?item[^"]*"[^>]*>(.*?)</div>\s*</div>',
        html, re.DOTALL
    )
    for block in blocks[:n]:
        titulo_m = re.search(r'<h[23][^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', block)
        if not titulo_m:
            titulo_m = re.search(r'<a[^>]+href="(/oferta[^"]+)"[^>]*>([^<]{5,80})</a>', block)
        if not titulo_m:
            continue

        link_raw = titulo_m.group(1)
        link     = link_raw if link_raw.startswith("http") else f"https://www.chiletrabajos.cl{link_raw}"
        titulo   = _clean(titulo_m.group(2))

        emp_m   = re.search(r'<span[^>]+class="[^"]*empresa[^"]*"[^>]*>([^<]+)', block)
        empresa = _clean(emp_m.group(1)) if emp_m else ""

        jobs.append({
            "id":          link,
            "titulo":      titulo,
            "empresa":     empresa,
            "descripcion": "",
            "link":        link,
            "ubicacion":   ubicacion,
            "fuente":      "chiletrabajos",
        })

    return jobs


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
def find_jobs(cargo: str, ubicacion: str, n: int = 40, trabajando_driver=None) -> list[dict]:
    """
    Busca empleos en todos los portales configurados.
    trabajando_driver: Selenium driver ya logueado en trabajando.cl (requerido para ese portal).
    Retorna lista de dicts con: id, titulo, empresa, descripcion, link, ubicacion, fuente.
    """
    all_jobs: list[dict] = []

    scrapers = [
        ("Trabajando.cl", lambda: _scrape_trabajando(cargo, ubicacion, n, driver=trabajando_driver)),
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
        # jobspy ya filtra por cargo — solo aplicar filtro estricto a scrapers HTML
        if j.get("fuente") in ("linkedin", "indeed"):
            filtered.append(j)
        else:
            # Al menos una palabra del cargo debe aparecer en el título
            if any(w in titulo for w in cargo_words if len(w) > 3):
                filtered.append(j)

    discarded = len(all_jobs) - len(filtered)
    if discarded:
        print(f"    [filtro relevancia] descartados {discarded} empleos irrelevantes")

    return filtered
