"""Mechanism-level invariants; no process, game calls, or live validation."""
import json
from pathlib import Path
import unittest

from baseline import (baseline_food_budget, homogeneous_food_consumption,
                      ordinary_food_consumption_per_person, BUILD, MODEL_VERSION)


class FoodBaselineTests(unittest.TestCase):
    def estimate(self, p=10, n=4, **kwargs):
        return baseline_food_budget(p, n, **kwargs)['model_estimate']

    def test_nonwinter_24_gross_per_gatherer_and_ten_people_consume_about_88(self):
        value = self.estimate()
        self.assertEqual(value['gross_food_per_game_month'], 96)
        self.assertAlmostEqual(value['food_consumption_per_game_month'], 87.975144, places=4)
        self.assertAlmostEqual(value['net_food_per_game_month'], 8.024856, places=4)

    def test_fixed_four_gatherers_change_from_surplus_to_deficit_in_ordinary_winter(self):
        value = self.estimate(clan='Raven', season='winter')
        self.assertAlmostEqual(value['gross_food_per_game_month'], 64)
        self.assertAlmostEqual(value['net_food_per_game_month'], -23.975144, places=4)

    def test_minimum_food_labor_shifts_with_winter_and_bear_rules(self):
        cases = [({}, 4), ({'clan': 'Raven', 'season': 'winter'}, 6),
                 ({'season': 'winter'}, 5), ({'season': 'winter', 'winter_festival': True}, 4)]
        for kwargs, expected in cases:
            with self.subTest(kwargs=kwargs):
                value = self.estimate(**kwargs)
                self.assertEqual(value['minimum_gatherers_for_nonnegative_steady_food'], expected)
                enough = self.estimate(n=expected, **kwargs)
                short = self.estimate(n=expected-1, **kwargs)
                self.assertGreaterEqual(enough['net_food_per_game_month'], 0)
                self.assertLess(short['net_food_per_game_month'], 0)

    def test_bear_reduces_penalty_amplitude_not_total_rate(self):
        ordinary = self.estimate(clan='Raven', season='winter')
        bear = self.estimate(season='winter')
        self.assertAlmostEqual(bear['gatherer_gross_food_per_game_second'], .3066666666666667)
        self.assertAlmostEqual(.4-bear['gatherer_gross_food_per_game_second'],
                               .7*(.4-ordinary['gatherer_gross_food_per_game_second']))

    def test_population_growth_costs_more_than_the_added_person_alone(self):
        for p in (4, 5, 10, 20, 30):
            with self.subTest(p=p):
                change = homogeneous_food_consumption(p+1)-homogeneous_food_consumption(p)
                self.assertGreater(change, ordinary_food_consumption_per_person(p+1))

    def test_reassignment_loses_food_but_does_not_charge_the_same_person_twice(self):
        before, after = self.estimate(n=4), self.estimate(n=3)
        self.assertEqual(before['food_consumption_per_game_month'], after['food_consumption_per_game_month'])
        self.assertAlmostEqual(after['net_food_per_game_month']-before['net_food_per_game_month'], -24)

    def test_winter_festival_changes_consumption_only_in_winter_food_model(self):
        off, on = self.estimate(season='winter'), self.estimate(season='winter', winter_festival=True)
        self.assertEqual(off['gross_food_per_game_month'], on['gross_food_per_game_month'])
        self.assertAlmostEqual(on['food_consumption_per_game_month'], .7*off['food_consumption_per_game_month'])
        self.assertEqual(self.estimate(), self.estimate(winter_festival=True))

    def test_population_winter_penalty_reaches_floor_after_thirty(self):
        a = self.estimate(p=30, season='winter', clan='Raven')
        b = self.estimate(p=60, season='winter', clan='Raven')
        self.assertAlmostEqual(a['gatherer_gross_food_per_game_second'], .24)
        self.assertEqual(a['gatherer_gross_food_per_game_second'], b['gatherer_gross_food_per_game_second'])

    def test_high_population_can_be_infeasible_without_other_bonuses(self):
        value = self.estimate(p=500, n=500)
        self.assertFalse(value['steady_food_self_sufficient_is_feasible'])
        self.assertIsNone(value['nonfood_workers_after_minimum_food_labor'])
        self.assertLess(value['net_food_per_game_month'], 0)

    def test_invalid_or_unsupported_inputs_do_not_become_zero_income(self):
        for p, n, kwargs in [(True, 1, {}), (0, 0, {}), (2**31-1, 1, {}), (10, False, {}), (10, 11, {}),
                             (10, 4, {'clan': 'Owl'}), (10, 4, {'context': 'Multiplayer'}),
                             (10, 4, {'season': None}),
                             (10, 4, {'clan': 'Raven', 'winter_festival': True})]:
            with self.subTest(p=p, n=n, kwargs=kwargs), self.assertRaises(ValueError):
                baseline_food_budget(p, n, **kwargs)

    def test_model_output_cannot_masquerade_as_native_production(self):
        value = baseline_food_budget(10, 4)
        self.assertFalse(value['is_native_measurement'])
        self.assertFalse(value['input_context_verified_by_this_module'])
        self.assertIn('actual_individual_settled_production', value['unknowns'])
        self.assertNotIn('native_actual_rate', json.dumps(value))

    def test_rule_registry_preserves_version_scope_and_unvalidated_status(self):
        rules = json.loads(Path(__file__).with_name('rules.v1.json').read_text(encoding='utf-8'))
        self.assertEqual(rules['build'], BUILD)
        self.assertEqual(rules['model_version'], MODEL_VERSION)
        ids = [row['id'] for row in rules['rules']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn('food.population_consumption_base', ids)
        self.assertEqual(rules['controlled_live_validation'], 'not_run')
        self.assertTrue(rules['baseline_conditions'])
        self.assertIn('native per-unit production or settlement', rules['unsupported'])


if __name__ == '__main__':
    unittest.main()
