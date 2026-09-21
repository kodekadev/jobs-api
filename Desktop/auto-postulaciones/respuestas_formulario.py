"""
Registro de las preguntas de formulario y lo que AplicAI respondió.

Antes esto se guardaba metiendo un JSON con prefijo "[QA]" dentro de la columna
`descripcion` de EMPLEOS, lo que ademas PISABA el texto del aviso. Y en la
practica solo lo escribia main.py, que no corre: habia 0 filas con Q&A sobre
25.253 postulaciones.

Ahora va a su propia tabla, una fila por pregunta, para poder responder:
  - que pregunta mas seguido cada portal
  - que responde AplicAI y de donde sale la respuesta (perfil / LLM / fallback)
  - que vio cada usuario en sus postulaciones

Nada de lo que hay aca puede tumbar una postulacion: todo va en try/except.
"""

TABLA = "jobs-425301.DWH.RESPUESTAS_FORMULARIO"

# Origen de cada respuesta
ORIGEN_PERFIL   = "perfil"    # salio de los datos del usuario (_standard_answer)
ORIGEN_LLM      = "llm"       # la genero Claude
ORIGEN_FALLBACK = "fallback"  # no se pudo responder, se uso un relleno

# Normaliza las etiquetas que usan los portales hoy
_ALIAS_ORIGEN = {
    "perfil": ORIGEN_PERFIL,
    "claude": ORIGEN_LLM,
    "llm": ORIGEN_LLM,
    "fallback": ORIGEN_FALLBACK,
}

_MAX_PREGUNTA = 500
_MAX_RESPUESTA = 2000


def normalizar_origen(origen) -> str:
    return _ALIAS_ORIGEN.get(str(origen or "").strip().lower(), ORIGEN_FALLBACK)


def construir_filas(pending: list, answers: dict, fallback_fn=None) -> "list[dict]":
    """
    Arma las filas a partir de la estructura que ya usan todos los portales:
      pending  -> [{"label": "...", ...}, ...]
      answers  -> {indice: (respuesta, origen)}

    Una pregunta sin etiqueta no sirve para nada despues, asi que se descarta.
    Funcion pura: no toca la red, para poder testearla.
    """
    filas = []
    for idx, item in enumerate(pending or []):
        pregunta = str((item or {}).get("label") or "").strip()
        if not pregunta:
            continue

        if idx in (answers or {}):
            respuesta, origen = answers[idx]
        else:
            respuesta = fallback_fn(item) if fallback_fn else ""
            origen = ORIGEN_FALLBACK

        filas.append({
            "pregunta": pregunta[:_MAX_PREGUNTA],
            "respuesta": str(respuesta or "")[:_MAX_RESPUESTA],
            "origen": normalizar_origen(origen),
        })
    return filas


def guardar(
    id_usuario: str,
    portal: str,
    id_empleo: str,
    titulo_empleo: str,
    filas: "list[dict]",
    postulo: bool = True,
) -> int:
    """Escribe una fila por pregunta. Devuelve cuantas guardo. Nunca lanza."""
    if not filas:
        return 0
    try:
        import bq
        from google.cloud import bigquery

        rows = [{
            "FECHA": None,  # lo pone el INSERT con CURRENT_TIMESTAMP
            "ID_USUARIO": id_usuario,
            "PORTAL": portal,
            "ID_EMPLEO": str(id_empleo or "")[:1024],
            "TITULO_EMPLEO": str(titulo_empleo or "")[:500],
            "PREGUNTA": f["pregunta"],
            "RESPUESTA": f["respuesta"],
            "ORIGEN": f["origen"],
            "POSTULO": bool(postulo),
        } for f in filas]

        valores, params = [], []
        for i, r in enumerate(rows):
            valores.append(
                f"(CURRENT_TIMESTAMP(), @u{i}, @p{i}, @e{i}, @t{i}, @q{i}, @a{i}, @o{i}, @s{i})"
            )
            params += [
                bigquery.ScalarQueryParameter(f"u{i}", "STRING", r["ID_USUARIO"]),
                bigquery.ScalarQueryParameter(f"p{i}", "STRING", r["PORTAL"]),
                bigquery.ScalarQueryParameter(f"e{i}", "STRING", r["ID_EMPLEO"]),
                bigquery.ScalarQueryParameter(f"t{i}", "STRING", r["TITULO_EMPLEO"]),
                bigquery.ScalarQueryParameter(f"q{i}", "STRING", r["PREGUNTA"]),
                bigquery.ScalarQueryParameter(f"a{i}", "STRING", r["RESPUESTA"]),
                bigquery.ScalarQueryParameter(f"o{i}", "STRING", r["ORIGEN"]),
                bigquery.ScalarQueryParameter(f"s{i}", "BOOL",   r["POSTULO"]),
            ]

        cfg = bigquery.QueryJobConfig(query_parameters=params)
        bq._query(f"""
            INSERT INTO `{TABLA}`
              (FECHA, ID_USUARIO, PORTAL, ID_EMPLEO, TITULO_EMPLEO,
               PREGUNTA, RESPUESTA, ORIGEN, POSTULO)
            VALUES {", ".join(valores)}
        """, cfg).result()

        print(f"    [qa] {len(rows)} respuesta(s) guardadas")
        return len(rows)
    except Exception as e:
        print(f"    [qa] No se pudieron guardar las respuestas: {e}")
        return 0


def guardar_desde(
    id_usuario: str,
    portal: str,
    id_empleo: str,
    titulo_empleo: str,
    pending: list,
    answers: dict,
    fallback_fn=None,
    postulo: bool = True,
) -> int:
    """Atajo: arma las filas y las guarda. Es lo que llaman los portales."""
    try:
        filas = construir_filas(pending, answers, fallback_fn)
    except Exception as e:
        print(f"    [qa] No se pudieron armar las respuestas: {e}")
        return 0
    return guardar(id_usuario, portal, id_empleo, titulo_empleo, filas, postulo)
