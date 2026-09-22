"""
Clasificación de preguntas de formularios de postulación.

Existe por un bug que mandó basura a producción: `_standard_answer` decidía
que una pregunta era "descriptiva" por los verbos INDIQUE / MENCIONE /
COMENTE, y devolvía el resumen del CV recortado a 400 caracteres. Los
formularios chilenos redactan casi todo así, de modo que preguntas de sí/no
terminaban contestadas con un ensayo sobre otra cosa:

    "Indique si posee licencia de conducir clase B vigente"
    -> "Ingeniero Civil Industrial con experiencia en Operaciones, Business..."

El 25,8% de las respuestas enviadas eran ese dump; un usuario mandó el mismo
texto en 23 preguntas distintas.

La regla acá es: el tipo se deduce del CONTENIDO y del INPUT, nunca del verbo
con que se redactó la pregunta. Y si no sabemos responder, se devuelve None
para que responda el modelo — o se salta la oferta. Nunca se rellena.
"""
import re
import unicodedata as _uc

SI_NO       = "si_no"
NUMERICA    = "numerica"
SELECCION   = "seleccion"
PRESENTACION = "presentacion"
TEXTO_LIBRE = "texto_libre"


def normalizar(texto: str) -> str:
    """Mayúsculas sin tildes. Igual que _norm_label de portal_accounts."""
    t = (texto or "").upper().strip()
    nfkd = _uc.normalize("NFKD", t)
    return _uc.normalize("NFKC", nfkd.translate({0x0301: None, 0x0308: None}))


# Verbos de redacción. NO determinan el tipo por sí solos: un formulario puede
# decir "Indique su renta" (numérica), "Indique si tiene licencia" (sí/no) o
# "Indique su experiencia en el rubro" (texto libre). Se usan solo para
# quitarlos del principio y mirar lo que viene después.
_VERBOS_REDACCION = [
    "INDIQUE", "INDICA", "INDICAR", "MENCIONE", "MENCIONA", "MENCIONAR",
    "COMENTE", "COMENTA", "COMENTAR", "SENALE", "SENALAR", "DETALLE",
    "DETALLA", "DETALLAR", "ESPECIFIQUE", "ESPECIFICA", "CONFIRME",
    "CONFIRMA", "RESPONDA", "RESPONDE", "FAVOR", "POR FAVOR",
]

# Arranques que piden un sí o un no, aunque después pidan ampliar.
_PATRONES_SI_NO = [
    r"\bCUENTA[S]? CON\b", r"\bCUENTA USTED CON\b",
    r"\bTIENE[S]?\b", r"\bTIENE USTED\b",
    r"\bPOSEE[S]?\b", r"\bDISPONE[S]?\b",
    r"\bHA TRABAJADO\b", r"\bHAS TRABAJADO\b", r"\bHA REALIZADO\b",
    r"\bHAS REALIZADO\b", r"\bHA GESTIONADO\b", r"\bHAS GESTIONADO\b",
    r"\bHA LIDERADO\b", r"\bHAS LIDERADO\b", r"\bHA MANEJADO\b",
    r"\bMANEJA[S]?\b", r"\bCONOCE[S]?\b", r"\bSABE[S]?\b",
    r"\bESTA[S]? DISPUESTO\b", r"\bESTARIA DISPUESTO\b",
    r"\bESTA[S]? DE ACUERDO\b", r"\bACEPTA[S]?\b",
    r"\bVIVE[S]? EN\b", r"\bRESIDE[S]? EN\b", r"\bRADICA[S]? EN\b",
    r"\bSE ENCUENTRA\b", r"\bTE INTERESA\b", r"\bLE INTERESA\b",
    r"\bCUENTAS\b", r"\bDOMINA[S]?\b",
    r"\bES USTED\b", r"\bERES\b",
    r"\bHA CURSADO\b", r"\bHAS CURSADO\b",
    r"\bTITULADO\b", r"\bDISPONIBILIDAD INMEDIATA\b",
    r"\bSI O NO\b", r"\bSI\/NO\b",
    # "…si posee…", "…si cuenta con…" tras un verbo de redacción
    r"\bSI (POSEE|CUENTA|TIENE|POSEES|CUENTAS|TIENES|POSEE USTED|POSEE UD)\b",
    r"\bSI (ESTA|ESTAS|ES|ERES|VIVE|VIVES|MANEJA|MANEJAS)\b",
]

# Lo que realmente pide un número.
# OJO: normalizar() quita tildes agudas pero NO la ñ, igual que _norm_label de
# portal_accounts. Por eso cada patrón con "AÑOS" necesita las dos formas.
_PATRONES_NUMERICA = [
    r"\bPRETENSION(ES)?\b", r"\bRENTA\b", r"\bSUELDO\b", r"\bSALARIO\b",
    r"\bSALARIAL\b", r"\bREMUNERACION\b",
    r"\bEXPECTATIVA[S]?\b.{0,20}\b(RENTA|SUELDO|SALARIO|SALARIAL|ECONOMICA)\b",
    r"\bCUANTOS? (ANOS|AÑOS|MESES|PERSONAS)\b",
    r"\bCUANTAS? (PERSONAS|HORAS)\b",
    r"\b(ANOS|AÑOS) DE EXPERIENCIA\b", r"\bEDAD\b",
    r"\bNUMERO DE (ANOS|AÑOS|PERSONAS)\b",
]

# Preguntas donde el resumen del candidato SÍ es la respuesta correcta.
_PATRONES_PRESENTACION = [
    r"\bCARTA DE PRESENTACION\b", r"\bPRESENTACION PERSONAL\b",
    r"\bCUENTANOS SOBRE (TI|USTED)\b", r"\bCUENTENOS SOBRE (TI|USTED)\b",
    r"\bHABLANOS DE (TI|USTED)\b", r"\bSOBRE (TI|MI|USTED)\b",
    r"\bACERCA DE (TI|MI|USTED)\b", r"\bDESCRIBASE\b", r"\bDESCRIBETE\b",
    r"\bPERFIL PROFESIONAL\b", r"\bRESUMEN PROFESIONAL\b",
    r"\bPOR QUE (TE INTERESA|QUIERES|POSTULAS|DESEAS)\b",
    r"\bMOTIVACION(ES)?\b", r"\bMOTIVA POSTULAR\b",
    r"\bTELL US ABOUT YOURSELF\b", r"\bCOVER LETTER\b",
    r"\bABOUT (YOU|YOURSELF|ME)\b", r"\bWHY (DO YOU|ARE YOU)\b",
    r"\bPERSONAL STATEMENT\b", r"\bINTRODUCE YOURSELF\b",
]

# Interrogativos que piden un dato, no un sí o un no. Mandan sobre los verbos
# de posesión: "¿Qué tipo de licencia de conducir tienes?" contiene TIENES pero
# se responde "B", no "Sí".
_PATRONES_ABIERTA = [
    r"^QUE\b", r"^CUAL(ES)?\b", r"^COMO\b", r"^DONDE\b", r"^CUANDO\b",
    r"^QUIEN(ES)?\b", r"\bQUE TIPO\b", r"\bQUE NIVEL\b", r"\bCUAL ES\b",
    r"\bCUALES SON\b", r"\bEN QUE\b", r"\bCON QUE\b", r"\bDE QUE\b",
]

_SI = {"SI", "SÍ", "YES", "S", "Y", "TRUE", "1", "AFIRMATIVO"}
_NO = {"NO", "N", "FALSE", "0", "NEGATIVO"}


def _sin_verbos(label_norm: str) -> str:
    """Quita el verbo de redacción inicial para mirar lo que de verdad pregunta."""
    t = label_norm
    for _ in range(3):  # "Por favor indique si..." puede encadenar dos
        cambio = False
        for v in _VERBOS_REDACCION:
            if t.startswith(v + " "):
                t = t[len(v) + 1:].lstrip(" ,:¿?")
                cambio = True
        if not cambio:
            break
    return t


def _opciones_son_si_no(options) -> bool:
    if not options:
        return False
    textos = {normalizar(str(o.get("text", o) if isinstance(o, dict) else o))
              for o in options}
    textos = {t for t in textos if t}
    if not (1 < len(textos) <= 3):
        return False
    return textos <= (_SI | _NO | {"NO APLICA", "N/A"})


def es_si_no(label: str, inp_type: str = "text", options=None) -> bool:
    """True si la pregunta se contesta con sí o no, aunque pida ampliar."""
    if _opciones_son_si_no(options):
        return True
    if options:           # tiene opciones pero no son sí/no -> es selección
        return False
    cuerpo = _sin_verbos(normalizar(label))
    if any(re.search(p, cuerpo) for p in _PATRONES_NUMERICA):
        return False
    if any(re.search(p, cuerpo) for p in _PATRONES_ABIERTA):
        return False
    return any(re.search(p, cuerpo) for p in _PATRONES_SI_NO)


def es_numerica(label: str, inp_type: str = "text", options=None) -> bool:
    if options:
        return False
    if (inp_type or "").lower() in ("number", "tel"):
        return True
    cuerpo = _sin_verbos(normalizar(label))
    return any(re.search(p, cuerpo) for p in _PATRONES_NUMERICA)


def es_presentacion(label: str) -> bool:
    cuerpo = _sin_verbos(normalizar(label))
    return any(re.search(p, cuerpo) for p in _PATRONES_PRESENTACION)


def clasificar(label: str, inp_type: str = "text", options=None) -> str:
    """
    Tipo de la pregunta. El orden importa: las opciones del formulario mandan
    sobre el texto, y lo numérico sobre lo interrogativo ("¿Cuántos años tiene
    de experiencia?" es número, no sí/no).
    """
    if _opciones_son_si_no(options):
        return SI_NO
    if options:
        return SELECCION
    if es_numerica(label, inp_type, options):
        return NUMERICA
    # Presentación antes que sí/no: "¿Por qué te interesa este cargo?" contiene
    # "TE INTERESA", que en cualquier otro contexto sí es una pregunta de sí/no.
    if es_presentacion(label):
        return PRESENTACION
    if es_si_no(label, inp_type, options):
        return SI_NO
    return TEXTO_LIBRE


def sanitizar_numero(valor, solo_digitos: bool = True) -> "str | None":
    """
    Deja un número apto para un campo numérico. "650000 brutos" -> "650000".
    Devuelve None si no hay ningún dígito: preferimos no responder a inventar.
    """
    if valor is None:
        return None
    digitos = re.sub(r"\D", "", str(valor))
    if not digitos:
        return None
    return digitos if solo_digitos else digitos


def normalizar_si_no(valor) -> "str | None":
    """
    Lleva la respuesta del modelo a "Sí" o "No". Acepta "Sí, tengo licencia
    clase B" porque el prompt pide ampliar. None si no empieza por ninguno:
    una respuesta que no resuelve el sí/no no sirve.
    """
    if valor is None:
        return None
    t = normalizar(str(valor)).lstrip(" ,.:;¡!¿?")
    if not t:
        return None
    primera = re.split(r"[\s,.:;!?]+", t)[0]
    if primera in _SI:
        return "Sí"
    if primera in _NO:
        return "No"
    return None


def opcion_valida(valor, options) -> "str | None":
    """
    Devuelve el value de la opción que corresponde, o None si la respuesta no
    coincide con ninguna. Nunca elige una opción al azar.
    """
    if not options or valor is None:
        return None
    objetivo = normalizar(str(valor))
    if not objetivo:
        return None
    for o in options:
        if isinstance(o, dict):
            texto, value = normalizar(str(o.get("text", ""))), str(o.get("value", ""))
        else:
            texto, value = normalizar(str(o)), str(o)
        if objetivo in (texto, normalizar(value)):
            return value or texto
    # Coincidencia parcial: el modelo a veces devuelve "SI" y la opción es "SI, CUENTO CON ELLA"
    for o in options:
        if isinstance(o, dict):
            texto, value = normalizar(str(o.get("text", ""))), str(o.get("value", ""))
        else:
            texto, value = normalizar(str(o)), str(o)
        if texto and (texto.startswith(objetivo) or objetivo.startswith(texto)):
            return value or texto
    return None


def validar_respuesta(valor, label: str, inp_type: str = "text", options=None) -> "str | None":
    """
    Valida la respuesta contra el tipo de la pregunta.
    None significa "no tenemos respuesta confiable": el que llama decide si
    manda la pregunta al modelo o salta la oferta. Nunca rellena.
    """
    if valor is None or str(valor).strip() == "":
        return None
    tipo = clasificar(label, inp_type, options)

    if tipo == SELECCION:
        return opcion_valida(valor, options)
    if tipo == SI_NO:
        if options:
            return opcion_valida(valor, options) or opcion_valida(
                normalizar_si_no(valor), options)
        # Campo de texto: basta con que resuelva el sí/no al empezar. El resto
        # de la frase se conserva — "Sí, poseo licencia clase B vigente" es
        # mejor respuesta que un "Sí" pelado, y es lo que pide el formulario.
        return str(valor).strip() if normalizar_si_no(valor) else None
    if tipo == NUMERICA:
        return sanitizar_numero(valor)
    return str(valor).strip()
