"""
Tests del texto de ofertas perdidas del correo resumen.

Ejecutar:  python test_notifier_texto.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from notifier import (
    _texto_ofertas_perdidas, puede_mostrar_upsell, MAX_UPSELL_SEMANA,
)


class TestTopeDeUpsell(unittest.TestCase):
    def test_no_se_muestra_si_no_topo_el_limite(self):
        # Antes aparecia en CADA resumen, dijera lo que dijera el numero
        self.assertFalse(puede_mostrar_upsell(False, 0))
        self.assertFalse(puede_mostrar_upsell(False, 99))

    def test_se_muestra_si_topo_y_no_alcanzo_el_tope(self):
        self.assertTrue(puede_mostrar_upsell(True, 0))
        self.assertTrue(puede_mostrar_upsell(True, MAX_UPSELL_SEMANA - 1))

    def test_no_se_muestra_al_alcanzar_el_tope(self):
        self.assertFalse(puede_mostrar_upsell(True, MAX_UPSELL_SEMANA))
        self.assertFalse(puede_mostrar_upsell(True, MAX_UPSELL_SEMANA + 5))

    def test_el_tope_son_dos_por_semana(self):
        self.assertEqual(MAX_UPSELL_SEMANA, 2)

    def test_historial_ilegible_no_bloquea_si_es_cero(self):
        # int() falla -> se asume 0 y se permite, el que corta es el caller
        self.assertTrue(puede_mostrar_upsell(True, None))
        self.assertTrue(puede_mostrar_upsell(True, "abc"))

    def test_una_semana_completa_de_topes_da_solo_dos(self):
        mostrados = 0
        for _ in range(7):                       # siete dias seguidos topando
            if puede_mostrar_upsell(True, mostrados):
                mostrados += 1
        self.assertEqual(mostrados, 2)


class TestTextoOfertasPerdidas(unittest.TestCase):
    def test_con_varias_ofertas_perdidas(self):
        html = _texto_ofertas_perdidas(7)
        self.assertIn("<strong>7</strong>", html)
        self.assertIn("ofertas más que no alcanzamos a enviar", html)

    def test_una_sola_va_en_singular(self):
        html = _texto_ofertas_perdidas(1)
        self.assertIn("<strong>1</strong>", html)
        self.assertIn("oferta más que no alcanzamos a enviar", html)
        self.assertNotIn("ofertas más", html)

    def test_sin_perdidas_no_dice_nada(self):
        # No queremos una linea que diga "0 ofertas"
        self.assertEqual(_texto_ofertas_perdidas(0), "")

    def test_negativo_no_dice_nada(self):
        self.assertEqual(_texto_ofertas_perdidas(-3), "")

    def test_sin_metricas_no_inventa(self):
        # Si el portal aun no se instrumento, preferimos callar
        for v in [None, "", "abc", [], {}]:
            self.assertEqual(_texto_ofertas_perdidas(v), "",
                             f"con {v!r} deberia devolver cadena vacia")

    def test_numero_como_texto(self):
        self.assertIn("<strong>4</strong>", _texto_ofertas_perdidas("4"))

    def test_el_html_esta_balanceado(self):
        html = _texto_ofertas_perdidas(5)
        self.assertEqual(html.count("<p"), 1)
        self.assertEqual(html.count("</p>"), 1)
        self.assertEqual(html.count("<strong>"), html.count("</strong>"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
