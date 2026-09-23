"""
Tests de bq.link_canonico.

El caso real: Computrabajo agrega #lc=ListOffers-Score6-NN con la posicion
de la oferta en la lista de resultados. Como cambia en cada busqueda, el
mismo empleo parecia nuevo cada noche y los duplicados en Mis Postulaciones
llegaron al 28% del volumen diario.
"""
import unittest
from bq import link_canonico


class TestFragmentoDeTracking(unittest.TestCase):
    BASE = "https://cl.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-en-4C04A4AA87228CD361373E686DCF3405"

    def test_mismo_empleo_distinta_posicion_en_la_lista(self):
        a = self.BASE + "#lc=ListOffers-Score6-16"
        b = self.BASE + "#lc=ListOffers-Score6-14"
        c = self.BASE + "#lc=ListOffers-Score6-7"
        self.assertEqual(link_canonico(a), link_canonico(b))
        self.assertEqual(link_canonico(b), link_canonico(c))
        self.assertEqual(link_canonico(a), self.BASE)

    def test_empleos_distintos_siguen_distintos(self):
        # 4C04... y 96B6... son dos avisos reales distintos, mismo titulo
        a = self.BASE + "#lc=ListOffers-Score6-16"
        b = ("https://cl.computrabajo.com/ofertas-de-trabajo/"
             "oferta-de-trabajo-en-96B624A90D4F00E561373E686DCF3405#lc=ListOffers-Score6-16")
        self.assertNotEqual(link_canonico(a), link_canonico(b))


class TestParametros(unittest.TestCase):
    def test_quita_tracking_conserva_lo_demas(self):
        u = "https://a.cl/oferta?utm_source=google&id=7&utm_medium=cpc"
        self.assertEqual(link_canonico(u), "https://a.cl/oferta?id=7")

    def test_sin_parametros_utiles_no_deja_interrogacion(self):
        self.assertEqual(link_canonico("https://a.cl/of?utm_source=x"), "https://a.cl/of")

    def test_conserva_parametros_que_identifican(self):
        u = "https://a.cl/ver?jobId=123&page=2"
        self.assertEqual(link_canonico(u), u)

    def test_quita_gclid_y_fbclid(self):
        self.assertEqual(link_canonico("https://a.cl/of?gclid=abc"), "https://a.cl/of")
        self.assertEqual(link_canonico("https://a.cl/of?fbclid=abc"), "https://a.cl/of")


class TestBordes(unittest.TestCase):
    def test_slash_final_no_hace_diferencia(self):
        self.assertEqual(link_canonico("https://a.cl/of/"), link_canonico("https://a.cl/of"))

    def test_vacios(self):
        self.assertEqual(link_canonico(""), "")
        self.assertEqual(link_canonico(None), "")

    def test_url_normal_no_se_toca(self):
        u = "https://cl.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-en-ABC123"
        self.assertEqual(link_canonico(u), u)

    def test_es_idempotente(self):
        u = self_u = "https://a.cl/of?utm_source=x#lc=ListOffers-1"
        una = link_canonico(u)
        self.assertEqual(link_canonico(una), una)


if __name__ == "__main__":
    unittest.main(verbosity=2)
