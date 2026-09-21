"""
Tests del texto de ofertas perdidas del correo resumen.

Ejecutar:  python test_notifier_texto.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from notifier import _texto_ofertas_perdidas


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
