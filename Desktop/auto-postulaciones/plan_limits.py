"""
Fuente única de los límites diarios de postulación.

Antes cada script tenía su propia copia de PLAN_LIMITS y no todas coincidían:
los scripts manuales (cht_/tbj_/lab_/cpt_/exc_postulando.py) no conocían
OWNER ni SPRINT, así que esos usuarios caían al fallback de FREE y recibían
5 postulaciones en vez de 75. Cualquier cambio de límite va SOLO aquí.
"""

PLAN_LIMITS: "dict[str, int]" = {
    "FREE":    5,
    "TRIAL":   10,
    "PRO":     25,
    "TURBO":   40,
    "SPRINT":  40,
    "PREMIUM": 50,
    "OWNER":   75,
}

PLAN_POR_DEFECTO = "FREE"

# Orden de procesamiento: primero quien paga más.
PLAN_ORDER: "dict[str, int]" = {
    "OWNER": 0, "PREMIUM": 1, "TURBO": 2, "SPRINT": 3,
    "PRO": 4, "TRIAL": 5, "FREE": 6,
}


def get_plan_limit(plan: "str | None") -> int:
    """Límite diario del plan. Un plan desconocido cae a FREE."""
    clave = (plan or PLAN_POR_DEFECTO).strip().upper()
    return PLAN_LIMITS.get(clave, PLAN_LIMITS[PLAN_POR_DEFECTO])


def get_daily_limit(user: dict) -> int:
    """
    Límite diario del usuario: su override individual si tiene uno válido,
    si no el de su plan. El override permite dar un cupo distinto a un
    usuario puntual sin tocar la tabla de planes.
    """
    if not isinstance(user, dict):
        return PLAN_LIMITS[PLAN_POR_DEFECTO]

    override = user.get("LIMITE_DIARIO", user.get("limite_diario"))
    if override is not None and not isinstance(override, bool):
        try:
            n = int(override)
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass

    return get_plan_limit(user.get("PLAN", user.get("plan")))


def plan_sort_key(user: dict) -> int:
    """Clave de orden por prioridad de plan, para procesar primero a quien paga."""
    plan = (user.get("PLAN") or user.get("plan") or PLAN_POR_DEFECTO).strip().upper()
    return PLAN_ORDER.get(plan, PLAN_ORDER[PLAN_POR_DEFECTO])
