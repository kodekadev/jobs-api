"""
Tests del matcher de cargos (_jau_match_score y sus ayudantes).

Ejecutar:  python test_jau_match.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from portal_accounts import (
    _jau_gender_variants,
    _jau_match_score,
    _JAU_SCORE_MINIMO,
)

PASA = _JAU_SCORE_MINIMO


class TestVariantes(unittest.TestCase):
    def test_genero_o_a(self):
        self.assertIn("ingeniera", _jau_gender_variants("ingeniero"))
        self.assertIn("kinesiologo", _jau_gender_variants("kinesiologa"))

    def test_plural_a_singular(self):
        self.assertIn("proyecto", _jau_gender_variants("proyectos"))
        self.assertIn("operacion", _jau_gender_variants("operaciones"))
        self.assertIn("venta", _jau_gender_variants("ventas"))
        self.assertIn("dato", _jau_gender_variants("datos"))
        self.assertIn("gestor", _jau_gender_variants("gestores"))

    def test_no_recorta_palabras_cortas(self):
        # 'mas' no debe convertirse en 'ma'
        self.assertEqual(_jau_gender_variants("mas"), ["mas"])

    def test_sin_duplicados(self):
        for w in ["proyectos", "ingeniero", "ventas", "analista"]:
            v = _jau_gender_variants(w)
            self.assertEqual(len(v), len(set(v)), f"{w} genero variantes repetidas")

    def test_la_palabra_original_siempre_esta_primero(self):
        self.assertEqual(_jau_gender_variants("proyectos")[0], "proyectos")


class TestPluralEnElMatch(unittest.TestCase):
    """El bug: un cargo en plural descartaba titulos en singular."""

    def test_caso_real_jobs2(self):
        # Del log de Laborum: se salto con score=0 siendo un match valido.
        score = _jau_match_score("Lider Tecnico de Proyecto - Santiago",
                                 ["Jefe de proyectos"])
        self.assertGreaterEqual(score, PASA)

    def test_singular_y_plural_dan_el_mismo_resultado(self):
        cargo = ["Jefe de proyectos"]
        s_sing = _jau_match_score("Lider Tecnico de Proyecto", cargo)
        s_plur = _jau_match_score("Lider Tecnico de Proyectos", cargo)
        self.assertGreaterEqual(s_sing, PASA)
        self.assertGreaterEqual(s_plur, PASA)

    def test_otros_cargos_con_sustantivo_plural(self):
        casos = [
            ("Jefe de operaciones", "Supervisor de Operacion"),
            ("Jefe de ventas",      "Supervisor de Venta"),
            ("Analista de datos",   "Especialista en Dato"),
        ]
        for cargo, titulo in casos:
            with self.subTest(cargo=cargo):
                self.assertGreaterEqual(_jau_match_score(titulo, [cargo]), PASA)


class TestNoRompeLoQueYaFuncionaba(unittest.TestCase):
    def test_match_exacto_sigue_en_100(self):
        self.assertEqual(_jau_match_score("Jefe de Proyectos", ["Jefe de proyectos"]), 100)

    def test_sigue_descartando_lo_que_no_calza(self):
        cargos = ["Jefe de proyectos", "product manager"]
        for titulo in ["Jefe de Turno (Lampa)", "Analista Control de Gestion",
                       "Auxiliar de Aseo", "Chofer Camion Tolva"]:
            with self.subTest(titulo=titulo):
                self.assertLess(_jau_match_score(titulo, cargos), PASA)

    def test_profesiones_de_salud_siguen_sin_cruzarse(self):
        # No revivir el bug del dominio compartido de salud.
        self.assertLess(
            _jau_match_score("Psicologo para Docencia", ["Terapeuta Ocupacional"]), PASA)
        self.assertLess(
            _jau_match_score("Nutricionista Clinica", ["Kinesiologo"]), PASA)

    def test_cargo_vacio_o_titulo_vacio(self):
        self.assertEqual(_jau_match_score("", ["Jefe de proyectos"]), 0)
        self.assertEqual(_jau_match_score("Jefe de Proyectos", []), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
