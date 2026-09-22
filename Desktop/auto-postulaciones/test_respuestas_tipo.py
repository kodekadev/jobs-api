"""
Tests de respuestas_tipo.

Los casos de SI_NO salen de preguntas reales que en producción se
contestaron con el resumen del CV recortado a 400 caracteres.
"""
import unittest

from respuestas_tipo import (
    SI_NO, NUMERICA, SELECCION, PRESENTACION, TEXTO_LIBRE,
    clasificar, es_si_no, es_numerica, es_presentacion,
    sanitizar_numero, normalizar_si_no, opcion_valida, validar_respuesta,
)


class TestPreguntasRealesQueFallaron(unittest.TestCase):
    """Cada una de estas recibió el resumen del CV en producción."""

    REALES = [
        "Indique si posee licencia de conducir clase B vigente",
        "¿Cuenta con licencia de conducir clase B y su respectiva hoja de vida del conductor?",
        "¿Cuenta con experiencia previa en cargos similares? Comente brevemente.",
        "Posee experiencia en implementación ERP BUK",
        "¿Tiene experiencia utilizando herramientas de IA (Copilot, ChatGPT, Claude u otros)?",
        "¿Ha trabajado con la plataforma Salesforce?",
        "¿Tiene experiencia revisando y validando contratos?",
        "Cuenta con experiencia en modelamiento de datos y KPI de RRHH",
        "¿Cuenta con manejo de Excel, Power BI, Python, SQL y Fabric? comente cuales",
        "Cuenta con conocimientos en control de inventarios y gestión de pañol?",
        "¿Cuenta con experiencia en las tecnologías solicitadas?",
        "¿Cuenta con experiencia liderando equipos técnicos en terreno dentro del rubro telecomunicaciones?",
        "La vacante requiere tener una cartera de clientes activa y comprobable ¿Cuenta con alguna? si es así, comentar",
        "¿Has gestionado márgenes, precios e inventarios tomando decisiones basadas en análisis de datos?",
        "¿Cuenta con experiencia en Call Center?",
    ]

    def test_todas_se_reconocen_como_si_no(self):
        fallan = [q for q in self.REALES
                  if clasificar(q, "textarea") != SI_NO]
        self.assertEqual(fallan, [], f"no clasificadas como si_no: {fallan}")

    def test_ninguna_se_clasifica_como_presentacion(self):
        # Si cae en PRESENTACION vuelve el dump del resumen.
        for q in self.REALES:
            self.assertNotEqual(clasificar(q, "textarea"), PRESENTACION, q)


class TestVerbosDeRedaccion(unittest.TestCase):
    """INDIQUE/MENCIONE/COMENTE ya no deciden el tipo por si solos."""

    def test_indique_con_sueldo_es_numerica(self):
        self.assertEqual(clasificar("Indica tus pretensiones de renta liquida"), NUMERICA)
        self.assertEqual(clasificar("Indicar pretensión de renta."), NUMERICA)

    def test_indique_con_si_es_si_no(self):
        self.assertEqual(clasificar("Indique si posee licencia clase B"), SI_NO)

    def test_indique_abierto_es_texto_libre(self):
        self.assertEqual(
            clasificar("Mencione su profesión y si tiene experiencia en dicha área.", "textarea"),
            SI_NO,  # "si tiene experiencia" domina: se contesta si/no + detalle
        )
        self.assertEqual(
            clasificar("Describa brevemente su experiencia en telecomunicaciones "
                       "indicando empresa, cargo y personas a cargo", "textarea"),
            TEXTO_LIBRE,
        )


class TestPreguntasAbiertas(unittest.TestCase):
    """Los interrogativos mandan sobre los verbos de posesión."""

    def test_que_tipo_no_es_si_no(self):
        # Contiene TIENES, pero se responde "B", no "Sí".
        self.assertNotEqual(
            clasificar("¿Qué tipo de licencia de conducir tienes?", "text"), SI_NO)

    def test_cual_es_no_es_si_no(self):
        self.assertNotEqual(
            clasificar("¿Cuál es el software de gestión que maneja?", "text"), SI_NO)

    def test_respuesta_corta_sobrevive(self):
        # Antes se descartaba por no empezar con Sí/No
        self.assertEqual(
            validar_respuesta("B", "¿Qué tipo de licencia de conducir tienes?", "text"),
            "B")

    def test_pero_tiene_licencia_si_es_si_no(self):
        self.assertEqual(clasificar("¿Tiene licencia de conducir clase B?", "text"), SI_NO)


class TestNumericas(unittest.TestCase):
    def test_renta(self):
        for q in ["¿Cuáles son sus expectativas de renta?",
                  "Indique su pretensión de renta",
                  "Sueldo esperado", "Expectativa salarial"]:
            self.assertEqual(clasificar(q, "textarea"), NUMERICA, q)

    def test_cuantos_anos(self):
        self.assertEqual(
            clasificar("¿Cuántos años de experiencia tiene supervisando cuadrillas?"),
            NUMERICA)

    def test_input_number_manda(self):
        self.assertEqual(clasificar("Lo que sea", "number"), NUMERICA)

    def test_no_confunde_rentabilidad(self):
        # "RENTA" con limite de palabra: RENTABILIDAD no es una pregunta de sueldo
        self.assertNotEqual(clasificar("¿Ha mejorado la rentabilidad de su area?", "textarea"),
                            NUMERICA)


class TestPresentacion(unittest.TestCase):
    def test_reconoce_presentacion(self):
        for q in ["Cuéntanos sobre ti", "Carta de presentación",
                  "¿Por qué te interesa este cargo?", "Tell us about yourself"]:
            self.assertEqual(clasificar(q, "textarea"), PRESENTACION, q)


class TestSeleccion(unittest.TestCase):
    def test_opciones_si_no_son_si_no(self):
        opts = [{"text": "Sí", "value": "1"}, {"text": "No", "value": "0"}]
        self.assertEqual(clasificar("Lo que sea", "radio", opts), SI_NO)

    def test_otras_opciones_son_seleccion(self):
        opts = [{"text": "Santiago", "value": "1"}, {"text": "Valparaíso", "value": "2"}]
        self.assertEqual(clasificar("Comuna", "select", opts), SELECCION)


class TestSanitizarNumero(unittest.TestCase):
    def test_quita_texto(self):
        self.assertEqual(sanitizar_numero("650000 brutos"), "650000")
        self.assertEqual(sanitizar_numero("$1.500.000"), "1500000")
        self.assertEqual(sanitizar_numero("2000000 líquidos"), "2000000")

    def test_sin_digitos_devuelve_none(self):
        # Preferimos no responder a inventar un numero
        self.assertIsNone(sanitizar_numero("a convenir"))
        self.assertIsNone(sanitizar_numero(""))
        self.assertIsNone(sanitizar_numero(None))


class TestNormalizarSiNo(unittest.TestCase):
    def test_acepta_si_con_detalle(self):
        self.assertEqual(normalizar_si_no("Sí, tengo licencia clase B vigente"), "Sí")
        self.assertEqual(normalizar_si_no("No"), "No")
        self.assertEqual(normalizar_si_no("SI"), "Sí")

    def test_rechaza_ensayo(self):
        # El bug original: un resumen de CV no resuelve un si/no
        self.assertIsNone(normalizar_si_no(
            "Durante más de siete años desarrollé mi carrera en operaciones"))
        self.assertIsNone(normalizar_si_no("Ingeniero Civil Industrial con experiencia"))


class TestOpcionValida(unittest.TestCase):
    OPTS = [{"text": "Sí", "value": "1"}, {"text": "No", "value": "0"}]

    def test_encuentra_por_texto(self):
        self.assertEqual(opcion_valida("Sí", self.OPTS), "1")
        self.assertEqual(opcion_valida("NO", self.OPTS), "0")

    def test_encuentra_por_value(self):
        self.assertEqual(opcion_valida("1", self.OPTS), "1")

    def test_no_inventa(self):
        self.assertIsNone(opcion_valida("Quizás", self.OPTS))
        self.assertIsNone(opcion_valida("", self.OPTS))

    def test_parcial(self):
        opts = [{"text": "Sí, cuento con ella", "value": "1"},
                {"text": "No", "value": "0"}]
        self.assertEqual(opcion_valida("Sí", opts), "1")


class TestValidarRespuesta(unittest.TestCase):
    def test_rechaza_dump_en_si_no(self):
        """El caso exacto que rompió produccion."""
        r = validar_respuesta(
            "Ingeniero Civil Industrial con experiencia en Operaciones, Business Intelligence...",
            "Indique si posee licencia de conducir clase B vigente", "textarea")
        self.assertIsNone(r)

    def test_rechaza_dump_en_renta(self):
        r = validar_respuesta(
            "Durante más de siete años desarrollé mi carrera en operaciones",
            "¿Cuales son sus expectativas de renta?", "textarea")
        self.assertIsNone(r)

    def test_limpia_numero_con_texto(self):
        self.assertEqual(
            validar_respuesta("650000 brutos",
                              "¿Cuáles son sus expectativas de Renta? Ingrese un número",
                              "number"),
            "650000")

    def test_acepta_si_no_y_conserva_el_detalle(self):
        # Un "Sí" pelado es peor respuesta que uno con el respaldo al lado.
        # Basta con que resuelva el sí/no al empezar.
        txt = "Sí, he trabajado con Salesforce durante 3 años"
        self.assertEqual(
            validar_respuesta(txt, "¿Ha trabajado con la plataforma Salesforce?", "textarea"),
            txt)

    def test_si_no_con_opciones_mapea_al_value(self):
        opts = [{"text": "Sí", "value": "1"}, {"text": "No", "value": "0"}]
        self.assertEqual(
            validar_respuesta("Sí, tengo licencia", "¿Tiene licencia?", "radio", opts),
            "1")

    def test_texto_libre_pasa_tal_cual(self):
        txt = "Lideré un equipo de 12 técnicos en Entel durante 4 años."
        self.assertEqual(
            validar_respuesta(txt, "Describa su experiencia liderando equipos", "textarea"),
            txt)

    def test_vacio_es_none(self):
        self.assertIsNone(validar_respuesta("", "lo que sea"))
        self.assertIsNone(validar_respuesta(None, "lo que sea"))
        self.assertIsNone(validar_respuesta("   ", "lo que sea"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
