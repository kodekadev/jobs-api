"""
Tests del armado de filas de Q&A. Sin red ni BigQuery.

Ejecutar:  python test_respuestas_formulario.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from respuestas_formulario import (
    construir_filas, normalizar_origen,
    ORIGEN_PERFIL, ORIGEN_LLM, ORIGEN_FALLBACK,
)


class TestNormalizarOrigen(unittest.TestCase):
    def test_etiquetas_que_usan_los_portales(self):
        # Los portales guardan "perfil", "Claude" y "fallback"
        self.assertEqual(normalizar_origen("perfil"), ORIGEN_PERFIL)
        self.assertEqual(normalizar_origen("Claude"), ORIGEN_LLM)
        self.assertEqual(normalizar_origen("llm"), ORIGEN_LLM)
        self.assertEqual(normalizar_origen("fallback"), ORIGEN_FALLBACK)

    def test_mayusculas_y_espacios(self):
        self.assertEqual(normalizar_origen("  CLAUDE  "), ORIGEN_LLM)

    def test_desconocido_o_vacio_cae_a_fallback(self):
        for v in [None, "", "   ", "otra_cosa", 123]:
            self.assertEqual(normalizar_origen(v), ORIGEN_FALLBACK)


class TestConstruirFilas(unittest.TestCase):
    def test_caso_tipico_de_un_portal(self):
        pending = [
            {"label": "¿Cuál es tu pretensión de renta?"},
            {"label": "¿Tienes experiencia en el rubro?"},
        ]
        answers = {0: ("1200000", "perfil"), 1: ("Sí, 6 años", "Claude")}

        self.assertEqual(construir_filas(pending, answers), [
            {"pregunta": "¿Cuál es tu pretensión de renta?",
             "respuesta": "1200000", "origen": ORIGEN_PERFIL},
            {"pregunta": "¿Tienes experiencia en el rubro?",
             "respuesta": "Sí, 6 años", "origen": ORIGEN_LLM},
        ])

    def test_pregunta_sin_responder_usa_el_fallback(self):
        pending = [{"label": "¿Disponibilidad inmediata?", "type": "textarea"}]
        filas = construir_filas(pending, {}, fallback_fn=lambda it: "Sí")
        self.assertEqual(filas, [{"pregunta": "¿Disponibilidad inmediata?",
                                  "respuesta": "Sí", "origen": ORIGEN_FALLBACK}])

    def test_sin_fallback_queda_respuesta_vacia_pero_se_registra(self):
        # Sirve para detectar formularios que no supimos responder
        filas = construir_filas([{"label": "Pregunta rara"}], {})
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["respuesta"], "")
        self.assertEqual(filas[0]["origen"], ORIGEN_FALLBACK)

    def test_descarta_preguntas_sin_etiqueta(self):
        pending = [{"label": ""}, {"label": "   "}, {}, {"label": "Válida"}]
        filas = construir_filas(pending, {3: ("ok", "perfil")})
        self.assertEqual([f["pregunta"] for f in filas], ["Válida"])

    def test_trunca_pregunta_y_respuesta(self):
        filas = construir_filas(
            [{"label": "P" * 900}], {0: ("R" * 5000, "Claude")})
        self.assertEqual(len(filas[0]["pregunta"]), 500)
        self.assertEqual(len(filas[0]["respuesta"]), 2000)

    def test_respuesta_no_string_no_rompe(self):
        filas = construir_filas([{"label": "Años"}], {0: (7, "perfil")})
        self.assertEqual(filas[0]["respuesta"], "7")

    def test_entradas_vacias(self):
        self.assertEqual(construir_filas([], {}), [])
        self.assertEqual(construir_filas(None, None), [])

    def test_los_indices_se_respetan(self):
        # answers puede tener huecos; cada respuesta debe ir a SU pregunta
        pending = [{"label": "A"}, {"label": "B"}, {"label": "C"}]
        answers = {0: ("ra", "perfil"), 2: ("rc", "Claude")}
        filas = construir_filas(pending, answers, fallback_fn=lambda it: "rb")
        self.assertEqual([(f["pregunta"], f["respuesta"]) for f in filas],
                         [("A", "ra"), ("B", "rb"), ("C", "rc")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
