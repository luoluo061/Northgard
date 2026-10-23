import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from research.telemetry import map_capture as module

PROFILE = {'hlboot_sha256': module.VERIFIED_HASH,
           'data_cdb_sha256': module.VERIFIED_CDB_HASH}
IDENTITY = dict(session_key='session', capture_run_id='run', capture_segment_id='run:1')


class FakeH:
    def __init__(self):
        self.calls = []
        self.types = {'me': 'ent.Player', 'state': 'GameState', 'game': 'Game',
                      'world': 'ent.player.WorldComponent',
                      **{f'z{i}': 'ent.Zone' for i in (1, 2, 3)},
                      **{f'c{i}': 'map.Cell' for i in (1, 2, 3)},
                      'terrain': 'enum:map.CellKind', 'generator': 'map.Generator'}
        self.fields = {
            ('state', 'me'): 'me', ('state', 'game'): 'game', ('state', 'biome'): 'Steppe',
            ('state', 'zones'): 'zones', ('me', 'world'): 'world',
            ('state', 'editor'): None, ('state', 'time'): 123., ('me', '__uid'): 31,
            ('world', 'discoveredIds'): 'known', ('game', 'state'): 'state',
            ('game', 'config'): None, ('game', 'generator'): None,
        }
        for i in (1, 2, 3):
            self.fields.update({(f'z{i}', 'id'): i, (f'z{i}', 'cell'): f'c{i}',
                                (f'c{i}', 'id'): i, (f'c{i}', 'kind'): 'terrain',
                                (f'c{i}', 'x'): i * 10., (f'c{i}', 'y'): i * 20.,
                                (f'c{i}', 'floor'): 0, (f'c{i}', 'hasLake'): False,
                                (f'c{i}', 'neighbors'): f'neighbors{i}'})
        self.arrays = {'zones': ['z1', 'z2', 'z3'], 'known': [1, 2],
                       'neighbors1': ['c2', 'c3'], 'neighbors2': ['c1', 'c3'],
                       'neighbors3': ['c1', 'c2']}

    def object_type(self, address):
        self.calls.append(('type', address))
        return SimpleNamespace(name=self.types[address])

    def field(self, address, name):
        self.calls.append(('field', address, name))
        return self.fields[(address, name)]

    def int_array(self, address):
        self.calls.append(('int_array', address))
        return list(self.arrays[address])


def run_capture(h, known=(1, 2), **kwargs):
    args = dict(known_zone_ids=known, identity=IDENTITY, profile=PROFILE)
    args.update(kwargs)
    with patch.object(module, '_objects', side_effect=lambda _, a: list(h.arrays[a])), \
         patch.object(module, '_string', side_effect=lambda _, value: value), \
         patch.object(module, 'enum_values', return_value=('Default', [])):
        return module.capture_map(h, 'me', 'state', **args)


class MapBoundaryTests(unittest.TestCase):
    def test_known_cells_only_and_unknown_neighbor_fields_not_read(self):
        h = FakeH()
        result = run_capture(h)
        self.assertEqual([r['id'] for r in result['known_zones']], [1, 2])
        self.assertEqual(result['known_zones'][0]['known_geometric_neighbor_ids'], [2])
        self.assertEqual(result['known_zones'][1]['known_geometric_neighbor_ids'], [1])
        self.assertFalse(any(c[0] == 'field' and c[1] == 'c3' for c in h.calls))
        self.assertFalse(any(c[0] == 'field' and c[1] == 'z3' and c[2] != 'id'
                             for c in h.calls))

    def test_unknown_topology_changes_do_not_change_output(self):
        h = FakeH()
        original = run_capture(h)
        h.arrays['neighbors1'] = ['c2', 'c100', 'c200', 'c300']
        h.arrays['neighbors2'] = ['c1']
        h.fields[('c3', 'owner')] = 'enemy'
        h.fields[('c3', 'remaining_minerals')] = 90000
        self.assertEqual(run_capture(h), original)

    def test_known_subset_has_no_boundary_degree_count(self):
        result = run_capture(FakeH(), known=(1,))
        tile = result['known_zones'][0]
        self.assertEqual(tile['known_geometric_neighbor_ids'], [])
        self.assertEqual(set(tile), {'id', 'terrain_kind', 'position', 'floor',
                                     'has_lake', 'known_geometric_neighbor_ids'})

    def test_never_reads_owners_buildings_units_or_dynamic_resources(self):
        h = FakeH()
        run_capture(h)
        forbidden = {'owner', 'buildings', 'units', 'foes', 'passiveUnits', 'decorators',
                     'ingredients', 'resources', 'maxBuildings', 'fog'}
        self.assertFalse(any(c[0] == 'field' and c[2] in forbidden for c in h.calls))

    def test_undiscovered_request_fails_closed(self):
        with self.assertRaises(module.LayoutError):
            run_capture(FakeH(), known=(1, 3))

    def test_duplicate_boolean_and_invalid_id_fail_closed(self):
        for requested in ((1, 1), (True,), (float('nan'),), ('1',)):
            with self.assertRaises(module.LayoutError):
                run_capture(FakeH(), known=requested)

    def test_missing_identity_or_build_mismatch_fails_closed(self):
        for key in IDENTITY:
            identity = {k: v for k, v in IDENTITY.items() if k != key}
            with self.assertRaises(module.LayoutError):
                run_capture(FakeH(), identity=identity)
        for key in PROFILE:
            with self.assertRaises(module.LayoutError):
                run_capture(FakeH(), profile={**PROFILE, key: 'unknown'})

    def test_missing_known_zone_or_cell_mismatch_fails_closed(self):
        h = FakeH()
        h.arrays['zones'].remove('z2')
        with self.assertRaises(module.LayoutError):
            run_capture(h)
        h = FakeH()
        h.fields[('c2', 'id')] = 99
        with self.assertRaises(module.LayoutError):
            run_capture(h)

    def test_invalid_coordinates_and_lake_flag_fail_closed(self):
        for name, value in (('x', float('nan')), ('hasLake', 1), ('floor', True)):
            h = FakeH()
            h.fields[('c1', name)] = value
            with self.assertRaises(module.LayoutError):
                run_capture(h)

    def test_unknown_terrain_enum_fails_closed(self):
        h = FakeH()
        h.types['terrain'] = 'enum:SomeOtherEnum'
        with self.assertRaises(module.LayoutError):
            run_capture(h)

    def test_editor_maps_are_not_assumed_static(self):
        h = FakeH()
        h.fields[('state', 'editor')] = 'editor'
        with self.assertRaises(module.LayoutError):
            run_capture(h)

    def test_map_identity_does_not_read_paths_or_full_map_size(self):
        h = FakeH()
        result = run_capture(h)
        self.assertEqual(result['map_identity']['map_instance_id'], 'session')
        self.assertIsNone(result['map_identity']['seed'])
        self.assertFalse(any(c[0] == 'field' and c[2] in ('saveFile', 'mapSize', 'worldSize')
                             for c in h.calls))


class SeedIdentityTests(unittest.TestCase):
    def test_two_seed_sources_remain_distinct(self):
        h = FakeH()
        h.fields.update({('game', 'config'): 'config', ('game', 'generator'): 'generator',
                         ('generator', 'game'): 'game', ('generator', 'seed'): 456})
        with patch.object(module, '_materialized', side_effect=['init', 123]) as lookup:
            result = module._seed_identity(h, 'game')
        self.assertEqual(result['seed'], 123)
        self.assertEqual(result['generation_seed'], 456)
        self.assertEqual([c.args[2] for c in lookup.call_args_list], ['init', 'seed'])

    def test_optional_virtual_seed_absence_is_unknown(self):
        h = FakeH()
        h.fields[('game', 'config')] = 'config'
        with patch.object(module, '_materialized', side_effect=module.LayoutError('not materialized')):
            result = module._seed_identity(h, 'game')
        self.assertIsNone(result['seed'])
        self.assertIsNone(result['generation_seed'])

    def test_generator_from_other_match_fails_closed(self):
        h = FakeH()
        h.fields[('game', 'generator')] = 'generator'
        h.fields[('generator', 'game')] = 'other_game'
        with self.assertRaises(module.LayoutError):
            module._seed_identity(h, 'game')


if __name__ == '__main__':
    unittest.main()
