"""
Tests de plan_limits. Sin dependencias externas.

Ejecutar:  python test_plan_limits.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from plan_limits import get_plan_limit, get_daily_limit, plan_sort_key, PLAN_LIMITS


class TestGetPlanLimit(unittest.TestCase):
    def test_planes_conocidos(self):
        self.assertEqual(get_plan_limit("FREE"), 5)
        self.assertEqual(get_plan_limit("TRIAL"), 10)
        self.assertEqual(get_plan_limit("PRO"), 25)
        self.assertEqual(get_plan_limit("TURBO"), 40)
        self.assertEqual(get_plan_limit("SPRINT"), 40)
        self.assertEqual(get_plan_limit("PREMIUM"), 50)
        self.assertEqual(get_plan_limit("OWNER"), 75)

    def test_normaliza_mayusculas_y_espacios(self):
        self.assertEqual(get_plan_limit("pro"), 25)
        self.assertEqual(get_plan_limit("  Premium  "), 50)

    def test_plan_desconocido_o_vacio_cae_a_free(self):
        for p in ["INEXISTENTE", "", None]:
            self.assertEqual(get_plan_limit(p), 5)


class TestGetDailyLimit(unittest.TestCase):
    def test_usa_el_limite_del_plan(self):
        self.assertEqual(get_daily_limit({"PLAN": "PRO"}), 25)
        self.assertEqual(get_daily_limit({"plan": "turbo"}), 40)

    def test_owner_y_sprint_no_caen_a_free(self):
        # Este era el bug: los scripts manuales no conocian estos planes.
        self.assertEqual(get_daily_limit({"PLAN": "OWNER"}), 75)
        self.assertEqual(get_daily_limit({"PLAN": "SPRINT"}), 40)

    def test_override_por_usuario_gana_sobre_el_plan(self):
        self.assertEqual(get_daily_limit({"PLAN": "FREE", "LIMITE_DIARIO": 30}), 30)
        self.assertEqual(get_daily_limit({"plan": "PRO", "limite_diario": 3}), 3)

    def test_override_invalido_cae_al_plan(self):
        for malo in [None, 0, -5, "", "abc", [], {}, True]:
            self.assertEqual(
                get_daily_limit({"PLAN": "PRO", "LIMITE_DIARIO": malo}), 25,
                f"override invalido {malo!r} deberia usar el limite del plan",
            )

    def test_override_numerico_como_texto(self):
        self.assertEqual(get_daily_limit({"PLAN": "FREE", "LIMITE_DIARIO": "12"}), 12)

    def test_usuario_sin_plan_ni_override(self):
        self.assertEqual(get_daily_limit({}), 5)

    def test_entrada_que_no_es_dict(self):
        for malo in [None, "PRO", 42, []]:
            self.assertEqual(get_daily_limit(malo), 5)


class TestPlanSortKey(unittest.TestCase):
    def test_ordena_de_mayor_a_menor_plan(self):
        users = [{"PLAN": p} for p in ["FREE", "OWNER", "PRO", "PREMIUM", "TRIAL"]]
        ordenados = [u["PLAN"] for u in sorted(users, key=plan_sort_key)]
        self.assertEqual(ordenados, ["OWNER", "PREMIUM", "PRO", "TRIAL", "FREE"])

    def test_plan_desconocido_va_al_final(self):
        users = [{"PLAN": "RARO"}, {"PLAN": "PRO"}]
        self.assertEqual(sorted(users, key=plan_sort_key)[0]["PLAN"], "PRO")


class TestCoherencia(unittest.TestCase):
    def test_todos_los_planes_tienen_limite_positivo(self):
        for plan, limite in PLAN_LIMITS.items():
            self.assertGreater(limite, 0, f"{plan} tiene limite no positivo")

    def test_los_valores_no_cambiaron(self):
        # Blindaje: si alguien cambia un limite sin querer, este test lo delata.
        self.assertEqual(PLAN_LIMITS, {
            "FREE": 5, "TRIAL": 10, "PRO": 25, "TURBO": 40,
            "SPRINT": 40, "PREMIUM": 50, "OWNER": 75,
        })


if __name__ == "__main__":
    unittest.main(verbosity=2)
