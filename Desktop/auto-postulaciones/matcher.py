"""
Filtrado inteligente de empleos contra el perfil del usuario.

Dos niveles:
  1. Filtro rápido por palabras clave (siempre activo, sin costo)
  2. Filtro LLM con Claude Haiku (si ANTHROPIC_API_KEY está configurado)

El LLM evalúa lotes de empleos en una sola llamada API para minimizar costo y latencia.
"""

import os
import json

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Palabras que rechazan el empleo inmediatamente sin importar el cargo
_REJECT_ALWAYS = [
    "práctica profesional", "practica profesional",
    "recién egresado", "recien egresado",
    "sin experiencia requerida", "sin experiencia",
    "primer empleo", "pasante", "practicante",
    "vendedor puerta a puerta", "venta directa",
    "multinivel", "network marketing",
]


def _parse_list(val) -> list:
    if not val:
        return []
    if isinstance(val, list):
        return val
    try:
        return json.loads(val)
    except Exception:
        return [str(val)]


def _keyword_filter(job: dict, user: dict) -> tuple[bool, str]:
    """Filtro rápido basado en palabras clave. Retorna (pasa, razón)."""
    titulo = (job.get("titulo") or "").lower()
    desc   = (job.get("descripcion") or "").lower()
    texto  = titulo + " " + desc

    # Rechazo duro — términos incompatibles con cualquier profesional
    for kw in _REJECT_ALWAYS:
        if kw in texto:
            return False, f"rechazado: '{kw}'"

    # Al menos una palabra relevante del cargo buscado debe aparecer en el título
    cargos = _parse_list(user.get("cargos") or user.get("CARGOS"))
    if not cargos:
        return True, "sin cargos definidos"

    cargo_words = set()
    for cargo in cargos:
        for w in cargo.lower().split():
            if len(w) > 3:
                cargo_words.add(w)

    hits = [w for w in cargo_words if w in titulo]
    if hits:
        return True, f"keyword match: {hits}"

    # Si el título no tiene la palabra del cargo, revisar si la fuente es jobspy
    # (jobspy ya filtró por cargo, así que confiamos en él)
    if job.get("fuente") in ("linkedin", "indeed"):
        return True, "jobspy ya filtró por cargo"

    return False, "título sin relación con cargos buscados"


def _build_profile_text(user: dict) -> str:
    cargos     = _parse_list(user.get("cargos") or user.get("CARGOS"))
    profesion  = user.get("profesion") or user.get("PROFESION") or ""
    resumen    = (user.get("resumen") or user.get("RESUMEN") or "")[:400]
    experiencia = user.get("experiencia") or user.get("EXPERIENCIA") or "no especificada"
    pretension = user.get("pretension_general") or user.get("PRETENSION_GENERAL") or ""
    try:
        pretension_fmt = f"${int(pretension):,} CLP" if pretension else ""
    except (ValueError, TypeError):
        pretension_fmt = f"${pretension} CLP" if pretension else ""

    base = (
        f"Profesión: {profesion}\n"
        f"Cargos buscados: {', '.join(cargos)}\n"
        f"Años/nivel de experiencia: {experiencia}\n"
        f"Resumen profesional: {resumen}"
    )
    return base + (f"\nPretensión salarial: {pretension_fmt}" if pretension_fmt else "")


def _llm_filter_batch(user: dict, jobs: list[dict]) -> list[int]:
    """
    Llama a Claude Haiku con un lote de empleos.
    Retorna lista de índices (dentro del lote) que son buen match.
    """
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    except ImportError:
        print("  [matcher] anthropic no instalado, usando solo keyword filter")
        return list(range(len(jobs)))

    perfil = _build_profile_text(user)

    empleos_txt = ""
    for i, job in enumerate(jobs):
        titulo = job.get("titulo", "")
        desc   = (job.get("descripcion") or "")[:300]
        empresa = job.get("empresa", "")
        empleos_txt += f"\n[{i}] {titulo} | {empresa}\n{desc}\n"

    prompt = f"""Evalúa si los siguientes empleos son apropiados para este candidato.

PERFIL DEL CANDIDATO:
{perfil}

EMPLEOS A EVALUAR:
{empleos_txt}

CRITERIOS DE MATCHING (en orden de prioridad):
1. ACEPTA SIEMPRE si el título del empleo contiene las mismas palabras clave del cargo buscado (p.ej. si busca "Jefe de proyectos" y el título dice "Jefe de Proyecto" o "Jefe(a) Proyecto" → ACEPTA).
2. RECHAZA SIEMPRE si el empleo es: práctica profesional, sin experiencia, pasantía, ventas directas, multinivel, retail operativo.
3. RECHAZA si la industria es claramente incompatible con la formación (p.ej. ingeniero civil industrial → no aplica a cargo de psicólogo, enfermera, chef).
4. En caso de duda, ACEPTA. Es mejor postular a un empleo borderline que perder una oportunidad.
5. Si la descripción está vacía, evalúa solo por título y sé generoso.

Responde SOLO con JSON válido: {{"match": [lista de índices que son buen match]}}
No incluyas texto adicional, solo el JSON."""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # Extraer JSON aunque venga con texto extra
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        data  = json.loads(raw[start:end])
        return [int(i) for i in data.get("match", [])]
    except Exception as e:
        print(f"  [matcher] LLM error: {e} — pasando todos los empleos")
        return list(range(len(jobs)))


def filter_jobs(user: dict, jobs: list[dict], verbose: bool = True) -> list[dict]:
    """
    Filtra la lista de empleos según el perfil del usuario.
    Primero aplica keyword filter, luego LLM si está disponible.
    Retorna solo los empleos que pasan el filtro.
    """
    if not jobs:
        return []

    # ── 1. Keyword filter (siempre) ────────────────────────────────────────────
    kw_passed = []
    kw_rejected = 0
    for job in jobs:
        ok, reason = _keyword_filter(job, user)
        if ok:
            kw_passed.append(job)
        else:
            kw_rejected += 1
            if verbose:
                print(f"  [matcher/kw] ✗ {job.get('titulo','')[:50]} — {reason}")

    if verbose:
        print(f"  [matcher] keyword: {len(kw_passed)} pasan / {kw_rejected} rechazados")

    if not kw_passed:
        return []

    # ── 2. LLM filter (solo si hay API key) ────────────────────────────────────
    if not ANTHROPIC_API_KEY:
        if verbose:
            print("  [matcher] Sin ANTHROPIC_API_KEY — usando solo keyword filter")
        return kw_passed

    BATCH = 15
    llm_passed = []
    for batch_start in range(0, len(kw_passed), BATCH):
        batch = kw_passed[batch_start:batch_start + BATCH]
        good_indices = _llm_filter_batch(user, batch)
        for i in good_indices:
            llm_passed.append(batch[i])
        llm_rejected = len(batch) - len(good_indices)
        if verbose and llm_rejected:
            for i, job in enumerate(batch):
                if i not in good_indices:
                    print(f"  [matcher/llm] ✗ {job.get('titulo','')[:50]}")

    if verbose:
        print(f"  [matcher] LLM: {len(llm_passed)} pasan / {len(kw_passed)-len(llm_passed)} rechazados")
        for j in llm_passed:
            fuente = j.get("fuente", "?")
            print(f"  [matcher/llm] ✓ [{fuente}] {j.get('titulo','')[:55]}")

    return llm_passed
