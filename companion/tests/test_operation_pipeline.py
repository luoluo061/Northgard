"""Integrated raw projection -> action diff -> ledger/situation regressions."""
from copy import deepcopy
import unittest

from companion.tests.test_player_observation import snapshot_with_economy, actor
from companion.tests.test_operation_projection import operations
from companion.core.player_observation import project_player_observation
from companion.core.action_diff import ActionDiffer
from companion.core.operation_ledger import OperationLedger
from companion.core.situation_timeline import SituationTracker
from research.telemetry.cost_quotes import quote_feast, HLBOOT_SHA256, DATA_CDB_SHA256

PROFILE = {'hlboot_sha256': HLBOOT_SHA256, 'data_cdb_sha256': DATA_CDB_SHA256}


def pair():
    a = snapshot_with_economy()
    a['sample_span_game_seconds'] = .01
    a['game_context'] = {'mode': {'kind': 'Singleplayer', 'args': [{'kind': 'FreeForAll', 'args': []}]},
                         'bifrost_data_present': False, 'editor_present': False}
    a['stocks'].update(Food=700, Wood=500, Money=600, Stone=30, Iron=10, Lore=50)
    a['operations'] = operations()
    a['operations']['colonizations'] = []
    a['operations']['building_costs'] = []
    for key in ('session_key', 'capture_run_id', 'capture_segment_id'):
        a['operations'][key] = a[key]
    a['operations']['feast']['instigator_is_self'] = None
    a['operations']['feast_quote'] = quote_feast(a['operations'], clan='Bear', game_context=a['game_context'], profile=PROFILE)
    for name in ('Food', 'Wood', 'Money'):
        a['economy']['resources'][name].update(stock=a['stocks'][name], gross_aggregate_per_game_second=2,
            consumption_per_game_second=1, net_per_game_second=1, total_produced_counter=50)
    b = deepcopy(a)
    b['game_time_seconds'] += 1
    b['operations']['game_time_seconds'] = b['game_time_seconds']
    b['operations']['time_before'] = b['operations']['time_after'] = b['game_time_seconds']
    b['economy']['game_time_seconds'] += 1
    b['economy']['time_before'] += 1
    b['economy']['time_after'] += 1
    for name in ('Food', 'Wood', 'Money'):
        b['stocks'][name] += 1
        b['economy']['resources'][name]['stock'] += 1
        b['economy']['resources'][name]['cached_at_game_time'] += 1
        b['economy']['resources'][name]['total_produced_counter'] += 2
    return a, b


def replay(a, b):
    a, b = (project_player_observation(raw, include_own_events=True) for raw in (a, b))
    assert a['operations']['status'] == b['operations']['status'] == 'ok'
    differ, ledger = ActionDiffer(), OperationLedger()
    ledger.feed(a, differ.feed(a))
    action = differ.feed(b)
    action.update({key: b[key] for key in ('session_key', 'capture_run_id', 'capture_segment_id')})
    return ledger.feed(b, action), action


class OperationPipelineTests(unittest.TestCase):
    def test_fifth_feast_cost_links_through_real_projection_and_native_adapter(self):
        a, b = pair()
        b['stocks']['Food'] -= 510
        b['economy']['resources']['Food']['stock'] -= 510
        b['operations']['feast'].update(feast_count=5, last_feast=121.2, instigator_is_self=True)
        b['operations']['feast_quote'] = quote_feast(b['operations'], clan='Bear', game_context=b['game_context'], profile=PROFILE)
        b['native_events'] = [{'index': 0, 'time': 121, 'kind': 'Feast', 'params': [actor()]}]
        result, action = replay(a, b)
        self.assertEqual(len(action['new_native_events']), 1)
        feast = next(c for c in result['operation_candidates'] if c['kind'] == 'feast_started')
        self.assertEqual(feast['cost'], [{'resource': 'Food', 'amount': 510}])
        self.assertEqual(feast['match_status'], 'cost_matches_observed_residual')
        self.assertAlmostEqual(result['resource_links']['Food']['unexplained_residual'], 0)

    def test_temple_keeps_mineral_uncertainty_while_matching_core_residuals(self):
        a, b = pair()
        b['buildings'].append({'uid': 21, 'zone_id': 87, 'kind': 'Construction',
                              'is_construction': True, 'target_kind': 'Temple'})
        cost = [{'resource': 'Wood', 'amount': 300}, {'resource': 'Money', 'amount': 300},
                {'resource': 'Stone', 'amount': 15}]
        b['operations']['building_costs'].append({'uid': 21, 'zone_id': 87, 'kind': 'Construction',
            'was_free': False, 'cost': cost, 'cost_record_status': 'stored_record'})
        for name, amount in (('Wood', 300), ('Money', 300), ('Stone', 15)):
            b['stocks'][name] -= amount
            if name in b['economy']['resources']:
                b['economy']['resources'][name]['stock'] -= amount
        result, _ = replay(a, b)
        temple = next(c for c in result['operation_candidates'] if c['kind'] == 'construction_started')
        self.assertEqual(temple['match_status'], 'partial_cost_match')
        self.assertEqual(temple['resource_matches']['Wood']['status'], 'amount_matches_usable_residual')
        self.assertIn('without_continuous_calibration', temple['resource_matches']['Stone']['status'])


if __name__ == '__main__':
    unittest.main()
