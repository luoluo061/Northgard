"""Exercise the recording loop with synthetic observations, never a game."""
from copy import deepcopy
import contextlib
import io
import itertools
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collector import collect
from companion.core.map_projection import HLBOOT_SHA256, DATA_CDB_SHA256


def observation(when=0, *, finished=False):
    identity = dict(session_key='synthetic-session', capture_run_id='synthetic-run',
                    capture_segment_id='synthetic-segment')
    graph = {1: [2], 2: [1, 3], 3: [2], 4: []}
    return {'schema_version': 1, 'observation_type': 'PlayerObservation',
        'policy_version': 'player-observation.v1', 'status': 'ok',
        'scope': 'local_player_and_owned_zones', **identity,
        'quality': {'identity_verified': True}, 'local_player_entity_uid': 10,
        'game_time_seconds': when, 'clan': 'Raven',
        'session_game_finished': finished, 'stocks': {'Food': 50, 'Wood': 100, 'Money': 25},
        'owned_zone_ids': [1], 'discovered_zone_ids': list(graph),
        'units': [{'uid': 101, 'kind': 'Villager', 'zone_id': 1}],
        'buildings': [{'uid': 201, 'kind': 'TownHall', 'zone_id': 1,
                       'is_construction': False}], 'tiles': [{'id': 1}],
        'map_observation': {'schema_version': 1, 'policy_version': 'player-map.v1',
            'status': 'ok', 'source': 'native_discovered_static_map_read_only',
            'scope': 'local_player_discovered_static_cells', **identity,
            'adjacency_semantics': 'geometric_map_cell_neighbors_not_traversability',
            'hlboot_sha256': HLBOOT_SHA256, 'data_cdb_sha256': DATA_CDB_SHA256,
            'local_player_entity_uid': 10, 'game_time_seconds': when,
            'map_identity': {'map_instance_id': identity['session_key']},
            'known_zones': [{'id': zid, 'known_geometric_neighbor_ids': adjacent}
                            for zid, adjacent in graph.items()]}}


class BaseRingCollectorTests(unittest.TestCase):
    def run_loop(self, rows):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'synthetic-capture'
            iterator = iter(rows)
            connection = SimpleNamespace(generation=1, close=Mock(),
                probe=SimpleNamespace(compatibility={}, profile={'game_build': 'synthetic'}))

            def sample():
                row = next(iterator)
                if isinstance(row, BaseException):
                    raise row
                return deepcopy(row)

            connection.sample = sample
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(collect, 'ProbeConnection', return_value=connection))
                stack.enter_context(patch.object(collect, 'local_setup_ready', return_value=True))
                stack.enter_context(patch.object(collect, 'project_player_observation',
                                                 side_effect=lambda row, **_: row))
                stack.enter_context(patch.object(collect, 'ActionDiffer',
                    return_value=SimpleNamespace(feed=lambda _: {})))
                stack.enter_context(patch.object(collect, 'reconcile_file', return_value={}))
                stack.enter_context(patch.object(collect.time, 'monotonic',
                                                 side_effect=itertools.count(0, .1)))
                stack.enter_context(patch.object(collect.time, 'sleep'))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                collect.main(['--out-dir', str(output)])
            streams = {name: [json.loads(line) for line in (output / name).read_text(
                encoding='utf-8').splitlines()] for name in ('player-observations.ndjson', 'health.ndjson')}
            for name in ('manifest.json', 'latest.json'):
                streams[name] = json.loads((output / name).read_text(encoding='utf-8'))
            self.assertTrue(connection.close.called)
            return streams

    def test_ring_component_reaches_stream_latest_health_and_manifest(self):
        result = self.run_loop([observation(finished=True)])
        component = result['player-observations.ndjson'][0]['base_neighborhood']
        self.assertEqual(component['ring1_zone_ids'], [2])
        self.assertEqual(component['ring2_zone_ids'], [3])
        self.assertEqual(component['known_unclassified_zone_ids'], [4])
        self.assertEqual(result['latest.json']['base_neighborhood'], component)
        health = result['health.ndjson'][0]
        self.assertEqual(health['base_neighborhood_status'], 'partial')
        self.assertEqual(health['component_diagnostics']['base_neighborhood']['source_status'], 'derived')
        summary = result['manifest.json']['last_base_neighborhood']
        self.assertEqual((summary['anchor_zone_id'], summary['known_ring1_count'],
                          summary['known_ring2_count']), (1, 1, 1))
        self.assertEqual(result['manifest.json']['component_health_counts']['base_neighborhood:partial'], 1)

    def test_read_error_and_non_match_break_continuity_and_never_reuse_old_map(self):
        end = observation(2, finished=True)
        del end['map_observation']
        result = self.run_loop([observation(), OSError('synthetic failure'),
                               {'status': 'not_in_match'}, end])
        components = [row['base_neighborhood'] for row in result['player-observations.ndjson']]
        self.assertEqual(components[-1]['continuity'], 'baseline_after_read_error')
        self.assertEqual(components[-1]['status'], 'unknown')
        self.assertEqual(components[-1]['ring1_zone_ids'], [])
        self.assertEqual(components[-1]['zones'], [])
        self.assertEqual([row['status'] for row in result['health.ndjson']],
                         ['ok', 'collector_read_error', 'not_in_match', 'ok'])

    def test_own_colonization_does_not_recenter_base(self):
        end = observation(1, finished=True)
        end['owned_zone_ids'] = [1, 2]
        end['buildings'] = [{'uid': 202, 'kind': 'TownHall', 'zone_id': 2,
                             'is_construction': False}]
        result = self.run_loop([observation(), end])
        component = result['latest.json']['base_neighborhood']
        self.assertEqual(component['ring0_zone_ids'], [1])
        self.assertEqual(component['ring1_zone_ids'], [2])
        self.assertIsNone(component['anchor']['town_hall_uid'])
        self.assertEqual(component['anchor']['town_hall_reference_status'], 'not_currently_observed')


if __name__ == '__main__':
    unittest.main()
