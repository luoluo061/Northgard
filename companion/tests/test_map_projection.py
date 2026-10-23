import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from map_projection import project_map, SOURCE, HLBOOT_SHA256, DATA_CDB_SHA256


def fixtures():
    identity = dict(session_key='session', capture_run_id='run', capture_segment_id='run:1')
    snapshot = {'status': 'ok', 'local_player_entity_uid': 31,
                'local_player_identity': {'verified': True, 'role': 'player', 'entity_uid': 31,
                                          'is_ai': False, 'in_player_list': True},
                'discovered_zone_ids': [1, 2], 'owned_zone_ids': [1],
                'game_context': {'editor_present': False}, 'game_time_seconds': 123., **identity}
    value = {'schema_version': 1, 'status': 'ok', 'source': SOURCE,
             'hlboot_sha256': HLBOOT_SHA256, 'data_cdb_sha256': DATA_CDB_SHA256,
             'local_player_entity_uid': 31, 'game_time_seconds': 123., **identity,
             'map_identity': {'map_instance_id': 'session', 'biome': 'Steppe',
                              'seed': 123, 'seed_source': 'Game.config.init.seed',
                              'generation_seed': None, 'generation_seed_source': None},
             'known_zones': [{'id': 1, 'terrain_kind': 'Default', 'position': {'x': 1., 'y': 2.},
                              'floor': 0, 'has_lake': False, 'known_geometric_neighbor_ids': [2]},
                             {'id': 2, 'terrain_kind': 'Coast', 'position': {'x': 2., 'y': 3.},
                              'floor': 1, 'has_lake': True, 'known_geometric_neighbor_ids': [1]}]}
    return value, snapshot


class MapProjectionTests(unittest.TestCase):
    def reject(self, value, snapshot):
        result = project_map(value, snapshot)
        self.assertEqual(result['status'], 'rejected')
        self.assertNotIn('known_zones', result)
        self.assertNotIn('map_identity', result)
        return result

    def test_valid_known_cells_and_unknown_seed(self):
        value, snapshot = fixtures()
        result = project_map(value, snapshot)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['map_identity']['seed'], 123)
        self.assertIsNone(result['map_identity']['generation_seed'])
        self.assertIn('map_identity.generation_seed', result['unknown_fields'])
        self.assertEqual(result['known_zones'], value['known_zones'])

    def test_all_three_parent_identity_keys_bound(self):
        for key in ('session_key', 'capture_run_id', 'capture_segment_id'):
            value, snapshot = fixtures()
            value[key] = 'other'
            self.reject(value, snapshot)
            value, snapshot = fixtures()
            del snapshot[key]
            self.reject(value, snapshot)

    def test_local_identity_is_verified_and_optional_raw_uid_restricts(self):
        for raw_uid in (32, True, '31', None):
            value, snapshot = fixtures()
            value['local_player_entity_uid'] = raw_uid
            self.reject(value, snapshot)
        value, snapshot = fixtures()
        del value['local_player_entity_uid']
        self.assertEqual(project_map(value, snapshot)['status'], 'ok')
        for field, bad in (('is_ai', True), ('verified', False), ('role', 'spectator')):
            value, snapshot = fixtures()
            snapshot['local_player_identity'][field] = bad
            self.reject(value, snapshot)

    def test_map_instance_must_match_parent_session(self):
        value, snapshot = fixtures()
        value['map_identity']['map_instance_id'] = 'other'
        self.reject(value, snapshot)
        value, snapshot = fixtures()
        snapshot['game_context']['biome'] = 'OtherBiome'
        self.reject(value, snapshot)

    def test_known_cells_cannot_expand_scope_or_duplicate(self):
        value, snapshot = fixtures()
        value['known_zones'][1]['id'] = 3
        self.reject(value, snapshot)
        value, snapshot = fixtures()
        value['known_zones'].append(copy.deepcopy(value['known_zones'][0]))
        self.reject(value, snapshot)

    def test_neighbor_ids_cannot_expand_scope_or_duplicate(self):
        for neighbors in ([3], [2, 2], [True], [1], '2'):
            value, snapshot = fixtures()
            value['known_zones'][0]['known_geometric_neighbor_ids'] = neighbors
            self.reject(value, snapshot)

    def test_safe_seed_types_and_exact_source_required(self):
        for seed in (True, 2**31, -(2**31)-1, '123', {}, float('nan')):
            value, snapshot = fixtures()
            value['map_identity']['seed'] = seed
            self.reject(value, snapshot)
        value, snapshot = fixtures()
        value['map_identity']['seed_source'] = 'C:/secret.dat'
        self.reject(value, snapshot)

    def test_adversarial_shape_does_not_raise_or_pass(self):
        for field, bad in (('terrain_kind', {}), ('terrain_kind', 'Volcano'),
                           ('floor', True), ('has_lake', 1), ('position', None),
                           ('position', {'x': 10**1000, 'y': 0}),
                           ('position', {'x': float('inf'), 'y': 0})):
            value, snapshot = fixtures()
            value['known_zones'][0][field] = bad
            self.reject(value, snapshot)

    def test_arbitrary_fields_diagnostics_and_hidden_dynamic_values_omitted(self):
        value, snapshot = fixtures()
        value['process_pointer'] = 0x1234567
        value['unknown_fields'] = ['C:/secret.dat']
        value['reason_codes'] = ['secret exception detail']
        value['map_identity']['saveFile'] = 'C:/secret.dat'
        value['known_zones'][1].update(owner_uid=99, remaining_minerals=9000,
                                       buildings=[{'kind': 'Secret'}], unknown_neighbor_count=12)
        result = project_map(value, snapshot)
        encoded = json.dumps(result)
        for banned in ('secret', 'Secret', 'process_pointer', 'owner_uid', 'remaining_minerals',
                       'buildings', 'unknown_neighbor_count', 'saveFile'):
            self.assertNotIn(banned, encoded)

    def test_projection_does_not_alias_mutable_source(self):
        value, snapshot = fixtures()
        result = project_map(value, snapshot)
        value['known_zones'][0]['position']['x'] = 999
        value['known_zones'][0]['known_geometric_neighbor_ids'].append(999)
        self.assertEqual(result['known_zones'][0]['position']['x'], 1.)
        self.assertEqual(result['known_zones'][0]['known_geometric_neighbor_ids'], [2])

    def test_build_clock_and_editor_guards(self):
        for key, bad in (('hlboot_sha256', 'other'), ('data_cdb_sha256', 'other'),
                         ('game_time_seconds', 100.)):
            value, snapshot = fixtures()
            value[key] = bad
            self.reject(value, snapshot)
        value, snapshot = fixtures()
        snapshot['game_context']['editor_present'] = True
        self.reject(value, snapshot)


if __name__ == '__main__':
    unittest.main()
