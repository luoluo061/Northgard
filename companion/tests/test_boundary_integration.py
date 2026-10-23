"""Integration boundaries for sanitized economy rows and component clocks."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from collector.collect import economic_row
from companion.core.operation_projection import project_operations
from companion.core.economy_reconcile import reconcile_pair

SPEC = importlib.util.spec_from_file_location(
    'operation_projection_fixture', Path(__file__).with_name('test_operation_projection.py'))
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


class BoundaryIntegrationTests(unittest.TestCase):
    def test_economic_row_uses_only_projected_parent_values(self):
        projected = {'status':'ok', 'stocks':{'Food':0.,'Wood':12.,'Money':24.},
                     'population':0, 'game_time_seconds':5., 'clan':'Bear',
                     'economy':{'status':'missing'}}
        raw = deepcopy(projected)
        raw['stocks']['private_account_marker'] = 'AUDIT_PRIVATE_MARKER'
        raw['population'] = {'account':'AUDIT_PRIVATE_MARKER'}
        raw['arbitrary_private_key'] = 'AUDIT_PRIVATE_MARKER'
        row = economic_row(raw, projected)
        self.assertNotIn('AUDIT_PRIVATE_MARKER', json.dumps(row))
        self.assertEqual(row['stocks']['Food'], 0.)
        self.assertEqual(row['population'], 0)

    def test_operations_cannot_quote_using_old_read_clock_and_new_timestamp(self):
        source = FIXTURE.with_quote(FIXTURE.operations())
        parent = FIXTURE.snapshot()
        source['time_before'] = source['time_after'] = 1.
        source['sample_span_game_seconds'] = 0.
        result = project_operations(source, parent)
        self.assertEqual(result['status'], 'rejected')
        self.assertNotEqual(result.get('feast_quote', {}).get('status'), 'verified')

    def test_first_feast_null_instigator_keeps_valid_quote(self):
        source = FIXTURE.operations()
        source['feast'].update(feast_count=0,last_feast=0.,free_feast_count=0,
                               free_feast=0)
        source['feast']['instigator_is_self'] = None
        FIXTURE.with_quote(source)
        source['feast_quote']['cost'] = [{'resource':'Food','amount':100}]
        result = project_operations(source, FIXTURE.snapshot())
        self.assertEqual(result['status'], 'ok')
        self.assertIsNone(result['feast']['instigator_is_self'])
        self.assertEqual(result['feast_quote']['status'], 'verified')
        self.assertEqual(result['feast_quote']['cost'], [{'resource':'Food','amount':100}])

    def test_fresh_native_integer_time_event_keeps_its_first_seen_interval(self):
        identities = {'session_key':'match-a','capture_run_id':'run-a','capture_segment_id':'run-a:1'}
        def point(time):
            resources = {name:{'stock':100+time-10.4,'cached_at_game_time':time,
                'age_game_seconds':0.,'net_per_game_second':1.,
                'gross_aggregate_per_game_second':2.,'consumption_per_game_second':1.,
                'total_produced_counter':200+2*(time-10.4)} for name in ('Food','Wood','Money')}
            return {'status':'ok',**identities,'game_time_seconds':time,
                'sample_span_game_seconds':0.,'economy':{'status':'ok',**identities,
                    'game_time_seconds':time,'sample_span_game_seconds':0.,
                    'hlboot_sha256':'test-build','resource_table_fingerprint':'test-table',
                    'resources':resources}}
        before, after = point(10.4), point(10.9)
        action = {'time_interval':{'start_game_seconds':10.4,'end_game_seconds':10.9},
                  'new_native_events':[{'kind':'Feast','time':10,'index':5,**identities}]}
        row = economic_row(after,after,action)
        result = reconcile_pair(before,row,events=row['own_success_events'])
        self.assertEqual(result['status'], 'not_reconcilable')
        for block in result['resources'].values():
            self.assertIn('potential_discrete_transaction_in_interval',block['reasons'])


if __name__ == '__main__':
    unittest.main()
