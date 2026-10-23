import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from economy_reconcile import CAPACITY_BUILD, reconcile_pair, summarize_reconciliation


def sample(t, *, stock, produced):
    return {'status': 'ok', 'session_key': 's', 'capture_run_id': 'r', 'capture_segment_id': 'r:1',
            'hlboot_sha256': CAPACITY_BUILD, 'resource_table_fingerprint': 'table',
            'game_time_seconds': t, 'sample_span_game_seconds': .01,
            'resources': {name: {'stock': stock if name == 'Food' else 100 + t * 2,
                                'cached_at_game_time': t, 'age_game_seconds': 0,
                                'net_per_game_second': 2, 'gross_aggregate_per_game_second': 5,
                                'consumption_per_game_second': 3,
                                'total_produced_counter': produced if name == 'Food' else 100 + t * 5,
                                'stock_cap': 500 if name == 'Food' else None,
                                'stock_cap_verified': name == 'Food',
                                'stock_cap_source': 'verified_native_getMax_formula',
                                'integration_order': 'consumption_then_production'}
                          for name in ('Food', 'Wood', 'Money')}}


class CapacityReconcileTests(unittest.TestCase):
    def test_at_cap_only_consumed_amount_is_replaced_and_counted(self):
        row = reconcile_pair(sample(10, stock=500, produced=100), sample(11, stock=500, produced=103))
        food = row['resources']['Food']
        self.assertEqual(row['status'], 'accepted')
        self.assertTrue(food['verified_capacity_model_used'])
        self.assertEqual(food['production_integral_estimate'], 5)
        self.assertEqual(food['production_applied_integral_estimate'], 3)
        self.assertEqual(food['production_clipped_integral_estimate'], 2)
        self.assertEqual(food['residual_unexplained'], 0)

    def test_crossing_cap_retains_only_available_space_after_consumption(self):
        food = reconcile_pair(sample(10, stock=499, produced=100),
                              sample(11, stock=500, produced=104))['resources']['Food']
        self.assertEqual(food['status'], 'accepted')
        self.assertEqual(food['production_applied_integral_estimate'], 4)
        self.assertEqual(food['production_clipped_integral_estimate'], 1)

    def test_counter_does_not_get_excused_by_stock_cap(self):
        food = reconcile_pair(sample(10, stock=500, produced=100),
                              sample(11, stock=500, produced=105))['resources']['Food']
        self.assertEqual(food['status'], 'not_reconcilable')
        self.assertIn('positive_counter_differs_from_rate_integral', food['reasons'])

    def test_unknown_build_formula_or_order_cannot_justify_clipping(self):
        for key, value in [('stock_cap_verified', False), ('stock_cap_source', 'guess'),
                           ('integration_order', 'production_then_consumption')]:
            a, b = sample(10, stock=500, produced=100), sample(11, stock=500, produced=103)
            for row in (a, b):
                row['resources']['Food'][key] = value
            food = reconcile_pair(a, b)['resources']['Food']
            self.assertEqual(food['status'], 'not_reconcilable')
            self.assertFalse(food['verified_capacity_model_used'])
        a, b = sample(10, stock=500, produced=100), sample(11, stock=500, produced=103)
        a['hlboot_sha256'] = b['hlboot_sha256'] = 'unverified'
        self.assertFalse(reconcile_pair(a, b)['resources']['Food']['verified_capacity_model_used'])

    def test_cap_change_floor_rate_change_and_cost_remain_unresolved(self):
        a, b = sample(10, stock=500, produced=100), sample(11, stock=500, produced=103)
        b['resources']['Food']['stock_cap'] = 800
        self.assertIn('stock_capacity_changed_in_interval', reconcile_pair(a, b)['resources']['Food']['reasons'])
        a, b = sample(10, stock=1, produced=100), sample(11, stock=3, produced=105)
        self.assertIn('consumption_floor_within_cycle_possible', reconcile_pair(a, b)['resources']['Food']['reasons'])
        a, b = sample(10, stock=500, produced=100), sample(11, stock=400, produced=103)
        self.assertAlmostEqual(reconcile_pair(a, b)['resources']['Food']['residual_unexplained'], -100)
        b = sample(11, stock=500, produced=103)
        b['resources']['Food']['gross_aggregate_per_game_second'] = 6
        self.assertEqual(reconcile_pair(a, b)['resources']['Food']['status'], 'not_reconcilable')

    def test_summary_separates_potential_applied_and_clipped_production(self):
        row = reconcile_pair(sample(10, stock=500, produced=100), sample(11, stock=500, produced=103))
        food = summarize_reconciliation([row])['resources']['Food']
        self.assertEqual(food['accepted_production_estimate_sum'], 5)
        self.assertEqual(food['accepted_production_applied_estimate_sum'], 3)
        self.assertEqual(food['accepted_production_clipped_estimate_sum'], 2)


if __name__ == '__main__':
    unittest.main()
