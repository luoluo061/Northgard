import importlib.util
from pathlib import Path
import struct
import unittest
from types import SimpleNamespace

PATH = Path(__file__).resolve().parent / 'economy_capture.py'
SPEC = importlib.util.spec_from_file_location('economy_capture', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeReader:
    def __init__(self, *, race=False):
        self.race = race
        self.timestamp_reads = 0
        self.arrays = {4: [10., 10., 10., 0., 0.],
                       5: [.4, .8, .2, 80., 90.],
                       6: [.3, .1, .05, 0., 0.]}
        self.fields = {(1, 'time'): 10.1, (2, 'lastResUpdate'): 4,
                       (2, 'lastResProd'): 5, (2, 'lastResCons'): 6,
                       (2, 'food'): 100., (2, 'wood'): 90., (2, 'money'): 80.,
                       (2, 'currentFood'): 1.1, (2, 'currentWood'): .7,
                       (2, 'currentMoney'): .15, (2, 'totalProduced'): 7}

    def object_type(self, address):
        return SimpleNamespace(name='ent.player.ResourcesComponent' if address == 2 else 'hl.types.ArrayBytes_Float')

    def field(self, address, name):
        if name == 'length' and address in self.arrays:
            return len(self.arrays[address])
        if name == 'bytes' and address in self.arrays:
            return address * 100
        return self.fields[address, name]

    def read(self, address, count):
        key = address // 100
        values = list(self.arrays[key])
        if key == 4:
            self.timestamp_reads += 1
            if self.race and self.timestamp_reads > 1:
                values[0] += .5
        return struct.pack('<' + 'd' * len(values), *values)

    def string_map(self, address):
        return {'Food': 12.5, 'Wood': 15., 'Money': 3.}


TABLE = [{'id': name, 'index': idx, 'isProductible': idx < 3,
          'props': {'converts': 'Food'} if idx >= 3 else {}}
         for idx, name in enumerate(('Food', 'Wood', 'Money', 'Wheat', 'Fish'))]


class EconomyCaptureTests(unittest.TestCase):
    def test_null_cache_is_not_ready_but_wrong_type_is_not_initialization(self):
        reader = FakeReader()
        with self.assertRaises(MODULE.EconomicCacheNotReady):
            MODULE.float_array(reader, 0)
        reader.object_type = lambda pointer: SimpleNamespace(name='unexpected.type')
        with self.assertRaises(MODULE.LayoutError) as failure:
            MODULE.float_array(reader, 4)
        self.assertNotIsInstance(failure.exception, MODULE.EconomicCacheNotReady)

    def sample(self, **options):
        return MODULE.capture_economy(FakeReader(**options), 2, 1, 10.1, TABLE,
                                      identity={'session_key': 'a', 'capture_run_id': 'b', 'capture_segment_id': 'c'},
                                      profile={'hlboot_sha256': MODULE.VERIFIED_HASH, 'game_build': '25096976'})

    def test_food_aggregate_uses_native_current_net_plus_consumption(self):
        sample = self.sample()
        food = sample['resources']['Food']
        self.assertAlmostEqual(food['gross_aggregate_per_game_second'], 1.4)
        self.assertEqual(food['raw_cached_production_per_game_second'], .4)
        self.assertEqual(sample['raw_resource_slots'][3]['raw_production_per_game_second'], 80.)
        self.assertFalse(food['food_base_cache_is_total'])

    def test_natural_food_cache_age_and_nonrefresh_are_retained(self):
        wheat = self.sample()['raw_resource_slots'][3]
        self.assertEqual(wheat['cached_at_game_time'], 0)
        self.assertEqual(wheat['age_game_seconds'], 10.1)
        self.assertEqual(wheat['refresh_semantics'], 'not_guaranteed_to_refresh_in_regular_cycle')

    def test_concurrent_refresh_is_flagged_not_integrated(self):
        sample = self.sample(race=True)
        self.assertEqual(sample['status'], 'read_race')
        self.assertIn('cache_refreshed_during_read', sample['read_guard_issues'])

    def test_identity_and_table_fingerprint_retained(self):
        sample = self.sample()
        self.assertEqual(sample['capture_segment_id'], 'c')
        self.assertEqual(len(sample['resource_table_fingerprint']), 64)
        different = [dict(row) for row in TABLE]
        different[3]['id'] = 'SomethingElse'
        self.assertNotEqual(MODULE.resource_schema(TABLE)[1], MODULE.resource_schema(different)[1])

    def test_unknown_build_and_ambiguous_resource_indices_rejected(self):
        with self.assertRaises(MODULE.LayoutError):
            MODULE.capture_economy(FakeReader(), 2, 1, 10.1, TABLE, profile={'hlboot_sha256': 'wrong'})
        with self.assertRaises(MODULE.LayoutError):
            MODULE.resource_schema([{'id': 'Food'}])


if __name__ == '__main__':
    unittest.main()
