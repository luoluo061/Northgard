import copy
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'core/economy_reconcile.py'
SPEC = importlib.util.spec_from_file_location('economy_reconcile', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def snapshot(time=10, cycle=10, **extra):
    resources = {}
    for name in MODULE.CORE:
        resources[name] = {'stock': 100 + cycle * 1.25,
                           'cached_at_game_time': cycle, 'age_game_seconds': time - cycle,
                           'net_per_game_second': 1.25,
                           'gross_aggregate_per_game_second': 2.0,
                           'raw_cached_production_per_game_second': .4 if name == 'Food' else 2.0,
                           'consumption_per_game_second': .75,
                           'total_produced_counter': 20 + cycle * 2.0,
                           'stock_cap': None}
    output = {'status': 'ok', 'session_key': 'session-a', 'capture_run_id': 'run-a',
              'capture_segment_id': 'segment-a', 'hlboot_sha256': 'verified-build',
              'resource_table_fingerprint': 'table-a', 'game_time_seconds': time,
              'sample_span_game_seconds': .01, 'resources': resources}
    output.update(extra)
    return output


class EconomyReconcileTests(unittest.TestCase):
    def test_known_constant_rates_close_stock_and_positive_counter(self):
        result = MODULE.reconcile_pair(snapshot(), snapshot(12, 12))
        self.assertEqual(result['status'], 'accepted')
        self.assertAlmostEqual(result['resources']['Food']['production_integral_estimate'], 4)
        self.assertAlmostEqual(result['resources']['Food']['consumption_integral_estimate'], 1.5)
        self.assertAlmostEqual(result['resources']['Food']['residual_unexplained'], 0)

    def test_uses_native_cycle_not_sampling_clock(self):
        result = MODULE.reconcile_pair(snapshot(10.4, 10), snapshot(12.9, 12))
        self.assertEqual(result['status'], 'accepted')
        self.assertAlmostEqual(result['sample_elapsed_game_seconds'], 2.5)
        self.assertEqual(result['resources']['Food']['native_cycle_elapsed_game_seconds'], 2)

    def test_each_resource_has_independent_cache_time(self):
        a, b = snapshot(10.4, 10), snapshot(12.4, 12)
        for row, clock in [(a, 9.5), (b, 11.5)]:
            row['resources']['Wood'].update(snapshot(clock, clock)['resources']['Wood'])
            row['resources']['Wood']['age_game_seconds'] = row['game_time_seconds'] - clock
        result = MODULE.reconcile_pair(a, b)
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['resources']['Wood']['native_cycle_start_game_time'], 9.5)

    def test_food_uses_aggregate_not_base_cache(self):
        result = MODULE.reconcile_pair(snapshot(), snapshot(11, 11))
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['resources']['Food']['production_integral_estimate'], 2)

    def test_unexplained_cost_remains_residual(self):
        a, b = snapshot(), snapshot(11, 11)
        b['resources']['Wood']['stock'] -= 50
        result = MODULE.reconcile_pair(a, b)
        self.assertEqual(result['resources']['Wood']['status'], 'not_reconcilable')
        self.assertAlmostEqual(result['resources']['Wood']['residual_unexplained'], -50)
        self.assertNotIn('cost', result['resources']['Wood'])

    def test_known_potential_transaction_is_not_forced_to_close(self):
        result = MODULE.reconcile_pair(snapshot(), snapshot(11, 11),
                                       events=[{'kind': 'Colonize', 'time': 10.5}])
        self.assertIn('potential_discrete_transaction_in_interval', result['resources']['Food']['reasons'])

    def test_actual_construction_diff_interval_and_identity_are_understood(self):
        event = {'kind': 'construction_started_observed',
                 'time_interval': {'start_game_seconds': 10, 'end_game_seconds': 11}}
        result = MODULE.reconcile_pair(snapshot(), snapshot(11, 11), events=[event])
        self.assertIn('potential_discrete_transaction_in_interval', result['resources']['Wood']['reasons'])
        event['capture_run_id'] = 'another-run'
        self.assertEqual(MODULE.reconcile_pair(snapshot(), snapshot(11, 11), events=[event])['status'], 'accepted')

    def test_paused_unchanged_snapshot(self):
        self.assertEqual(MODULE.reconcile_pair(snapshot(), snapshot())['status'], 'paused')

    def test_counter_or_stock_changes_during_pause_are_rejected(self):
        b = snapshot()
        b['resources']['Money']['stock'] += 1
        result = MODULE.reconcile_pair(snapshot(), b)
        self.assertEqual(result['status'], 'not_reconcilable')
        self.assertEqual(result['resources']['Money']['residual_unexplained'], 1)

    def test_short_sample_without_new_cycle_is_neither_income_nor_pause(self):
        result = MODULE.reconcile_pair(snapshot(), snapshot(10.2, 10))
        self.assertEqual(result['status'], 'no_new_cycle')

    def test_cross_session_run_or_segment_rejected(self):
        for key in MODULE.IDENTITY:
            with self.subTest(key=key):
                b = snapshot(11, 11)
                b[key] = 'different'
                self.assertIn('different_' + key, MODULE.reconcile_pair(snapshot(), b)['reasons'])

    def test_missing_identity_rejected(self):
        b = snapshot(11, 11)
        del b['capture_segment_id']
        self.assertEqual(MODULE.reconcile_pair(snapshot(), b)['status'], 'not_reconcilable')

    def test_stale_cache_rejected(self):
        result = MODULE.reconcile_pair(snapshot(12, 10), snapshot(13, 11))
        self.assertIn('stale_or_future_cache', result['resources']['Food']['reasons'])

    def test_rate_transition_is_uncertain_even_when_endpoint_integral_happens_to_close(self):
        a, b = snapshot(), snapshot(11, 11)
        a['resources']['Food']['net_per_game_second'] = 1
        result = MODULE.reconcile_pair(a, b)
        self.assertIn('endpoint_rates_changed_unknown_transition_time', result['resources']['Food']['reasons'])

    def test_counter_delta_not_assumed_to_be_worker_production(self):
        a, b = snapshot(), snapshot(11, 11)
        b['resources']['Food']['total_produced_counter'] += 5
        result = MODULE.reconcile_pair(a, b)
        self.assertEqual(result['resources']['Food']['status'], 'not_reconcilable')
        self.assertAlmostEqual(result['resources']['Food']['positive_counter_minus_production_estimate'], 5)

    def test_clipped_stock_is_unreconcilable(self):
        a, b = snapshot(), snapshot(11, 11)
        b['resources']['Food']['stock_cap'] = b['resources']['Food']['stock']
        self.assertIn('stock_cap_or_clipping_possible', MODULE.reconcile_pair(a, b)['resources']['Food']['reasons'])

    def test_rollbacks_and_large_gaps_rejected(self):
        self.assertIn('game_time_rollback', MODULE.reconcile_pair(snapshot(), snapshot(9, 9))['reasons'])
        self.assertIn('sampling_gap_too_large', MODULE.reconcile_pair(snapshot(), snapshot(20, 20))['reasons'])

    def test_racing_or_very_wide_snapshot_rejected(self):
        self.assertEqual(MODULE.reconcile_pair(snapshot(), snapshot(11, 11, status='read_race'))['status'], 'not_reconcilable')
        self.assertEqual(MODULE.reconcile_pair(snapshot(), snapshot(11, 11, sample_span_game_seconds=.8))['status'], 'not_reconcilable')

    def test_nested_action_snapshot_supported(self):
        a, b = snapshot(), snapshot(11, 11)
        wrapper_a = {'status': 'ok', 'economy': a}
        wrapper_b = {'status': 'ok', 'economy': b}
        self.assertEqual(MODULE.reconcile_pair(wrapper_a, wrapper_b)['status'], 'accepted')

    def test_invalid_parent_or_conflicting_identity_cannot_be_hidden_by_child(self):
        a, b = snapshot(), snapshot(11, 11)
        wrapper_a = {'status': 'read_error', 'economy': a}
        wrapper_b = {'status': 'ok', 'economy': b}
        self.assertEqual(MODULE.reconcile_pair(wrapper_a, wrapper_b)['status'], 'not_reconcilable')
        wrapper_a = {'status': 'ok', 'capture_run_id': 'conflicting', 'economy': a}
        self.assertEqual(MODULE.reconcile_pair(wrapper_a, wrapper_b)['status'], 'not_reconcilable')

    def test_stream_breaks_on_error_and_preserves_acquisition_order(self):
        rows = [snapshot(), {'status': 'read_error'}, snapshot(11, 11), snapshot(12, 12)]
        results = list(MODULE.reconcile_stream(rows))
        self.assertEqual([r['status'] for r in results], ['not_reconcilable', 'accepted'])
        self.assertEqual(results[-1]['game_time_start'], 11)
        results = list(MODULE.reconcile_stream([snapshot(), snapshot(9, 9)]))
        self.assertIn('game_time_rollback', results[0]['reasons'])

    def test_summary_does_not_count_rejected_seconds_as_observed_production(self):
        rows = [snapshot(), snapshot(11, 11), snapshot(12, 12, status='read_race'), snapshot(13, 13)]
        summary = MODULE.summarize_reconciliation(MODULE.reconcile_stream(rows))
        self.assertEqual(summary['resources']['Food']['accepted_native_cycle_seconds'], 1)
        self.assertEqual(summary['resources']['Food']['accepted_production_estimate_sum'], 2)


if __name__ == '__main__':
    unittest.main()
