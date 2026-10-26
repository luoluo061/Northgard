"""Whole ActionProbe component-isolation regressions with a fake read-only heap.

No process is opened and no game state is required.
"""
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import action_capture as ac


class FakeHeap:
    fields = {
        1: {'inst': 2},
        2: {'state': 3},
        3: {'me': 4, 'time': 100., '__uid': 99, 'loadTime': 1.,
            'players': 5, 'zones': 7, 'difficulty': 1, 'biome': 0,
            'bifrostData': 0, 'editor': 0, 'gameFinished': False},
        4: {'__uid': 31, 'aiJob': 0, 'units': 6, 'res': 8,
            'gameOver': False, 'hasConceded': False, 'defeatedTime': 0.},
    }

    def field(self, ptr, name):
        return self.fields[ptr][name]

    def type_info(self, address):
        return SimpleNamespace(parameter=0)

    def object_type(self, address):
        return SimpleNamespace(name='ent.Player' if address == 4 else 'not-needed')


def probe():
    instance = ac.ActionProbe.__new__(ac.ActionProbe)
    instance.h = FakeHeap()
    instance.r = SimpleNamespace(pid=123, u64=lambda pointer: 1)
    instance.base = 1000
    instance.profile = {'types': {'Game': 803}, 'game_build': 'test'}
    instance.native_events = lambda state, session: []
    instance.identity_tracker = ac.CaptureIdentityTracker()
    instance.value = lambda ptr, field: {'kind': 'Singleplayer', 'args': []}
    return instance


class OptionalComponentsTests(unittest.TestCase):
    def test_null_economic_cache_is_not_ready_without_fabricated_zero_rates(self):
        row = self.sample(economy_error=ac.EconomicCacheNotReady('private exception text'))
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['economy']['status'], 'not_ready')
        self.assertEqual(row['economy']['reason_codes'], ['economic_cache_not_initialized'])
        self.assertNotIn('resources', row['economy'])
        self.assertNotIn('private', str(row['economy']))

    def test_guard_failure_has_a_static_reason_without_exception_message(self):
        with patch.object(ac, 'capture_growth', side_effect=ValueError('TownHall is not bound to the local player')):
            row = self.sample()
        self.assertIn('growth_town_hall_player_reference_conflict', row['population_growth']['reason_codes'])
        self.assertNotIn('error', row['population_growth'])

    def test_new_labor_and_growth_failures_do_not_discard_core_snapshot(self):
        with (patch.object(ac, 'capture_labor', side_effect=TypeError('private path C:/do-not-export')),
              patch.object(ac, 'capture_growth', side_effect=ValueError('private address 0xDEADBEEF'))):
            row = self.sample()
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['labor']['status'], 'unavailable')
        self.assertEqual(row['population_growth']['status'], 'unavailable')
        self.assertNotIn('do-not-export', str(row['labor']))
        self.assertNotIn('DEADBEEF', str(row['population_growth']))

    def sample(self, *, operations_error=None, quote_error=None, map_error=None,
               economy_error=None):
        operations = {'status': 'ok'}
        with patch.object(ac.Probe, 'sample', return_value={'status':'ok',
                    'clan':'Bear', 'discovered_zone_ids':[]}), \
             patch.object(ac, 'objects', side_effect=lambda h, p, **kwargs: [4] if p == 5 else []), \
             patch.object(ac, 'string', return_value='Meadows'), \
             patch.object(ac, 'capture_operations', side_effect=operations_error,
                          return_value=operations), \
             patch.object(ac, 'quote_feast', side_effect=quote_error,
                          return_value={'status':'unsupported'}), \
             patch.object(ac, 'capture_map', side_effect=map_error,
                          return_value={'status':'ok'}), \
             patch.object(ac, 'capture_economy', side_effect=economy_error,
                          return_value={'status':'ok'}):
            return probe().sample()

    def test_optional_operation_decoder_failure_keeps_core_snapshot(self):
        row = self.sample(operations_error=struct.error('private path 0xDEADBEEF'))
        self.assertEqual(row['status'], 'ok')
        self.assertNotEqual(row['operations']['status'], 'ok')
        self.assertEqual(row['economy']['status'], 'ok')
        self.assertEqual(row['map_observation']['status'], 'ok')
        self.assertNotIn('DEADBEEF', str(row['operations']))

    def test_quote_failure_keeps_other_components(self):
        row = self.sample(quote_error=ValueError('private formula path'))
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['economy']['status'], 'ok')
        self.assertEqual(row['map_observation']['status'], 'ok')
        self.assertNotIn('private formula path', str(row['operations']))

    def test_optional_map_failure_keeps_core_and_operations(self):
        row = self.sample(map_error=struct.error('private path 0xDEADBEEF'))
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['operations']['status'], 'ok')
        self.assertEqual(row['economy']['status'], 'ok')
        self.assertEqual(row['map_observation']['status'], 'unavailable')
        self.assertNotIn('DEADBEEF', str(row['map_observation']))

    def test_optional_economy_failure_keeps_core_and_map(self):
        row = self.sample(economy_error=struct.error('private path'))
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['operations']['status'], 'ok')
        self.assertEqual(row['map_observation']['status'], 'ok')
        self.assertEqual(row['economy']['status'], 'read_error')


if __name__ == '__main__':
    unittest.main()
