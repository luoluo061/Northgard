import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from research.telemetry import growth_capture as module

PROFILE = {'hlboot_sha256': module.VERIFIED_HASH, 'data_cdb_sha256': module.VERIFIED_CDB_HASH}
IDENTITY = {'session_key': 'session', 'capture_run_id': 'run', 'capture_segment_id': 'run:1'}


class FakeH:
    def __init__(self):
        self.calls = []
        self.types = {'me': 'ent.Player', 'state': 'GameState',
                      'res': 'ent.player.ResourcesComponent', 'hall': 'ent.b.TownHall',
                      'house': 'ent.Building', 'z1': 'ent.Zone', 'flags': 'hxbit.EnumFlagsData',
                      'tech': 'ent.player.TechComponent', 'map_proxy': 'hxbit.MapData',
                      'map': 'haxe.ds.StringMap'}
        self.fields = {
            ('state', 'me'): 'me', ('state', 'time'): 123., ('state', 'gameFinished'): False,
            ('me', 'aiJob'): None, ('me', 'res'): 'res', ('res', 'entity'): 'me',
            ('me', '__uid'): 31, ('me', 'clan'): 'Bear', ('me', 'hasConceded'): False,
            ('me', 'gameOver'): False, ('me', 'debug'): 'debug',
            ('debug', 'populationCanGrow'): True, ('me', 'modifierFlags'): 'flags',
            ('flags', 'value'): 0, ('me', 'tech'): 'tech',
            ('tech', 'researchedTechs'): 'map_proxy', ('map_proxy', 'map'): 'map',
            ('tech', 'allFountainResearches'): None, ('me', 'owl'): None,
            ('me', 'cook'): 'cook', ('cook', 'activeMeals'): None,
            ('me', 'bonuses'): None, ('state', 'additionalRules'): None,
            ('hall', 'zone'): 'z1', ('house', 'zone'): 'z1',
            ('z1', 'id'): 1, ('z1', 'owner'): 'me', ('hall', 'player'): 'me',
            ('hall', '__uid'): 100, ('hall', 'growth'): 33.,
            ('hall', 'forceRecruit'): False, ('hall', 'recruitStack'): 0.,
            ('hall', 'enabled'): True,
        }
        for key, (field, kind) in module.NATIVE_FIELDS.items():
            self.fields['res', field] = (False if kind == 'bool' else 0)
        self.fields.update({('res', 'growth'): 0., ('res', 'currentHappiness'): 4.,
                            ('res', 'population'): 10, ('res', 'nbMaxPop'): 16})

    def field(self, address, name):
        self.calls.append(('field', address, name))
        return self.fields[address, name]

    def object_type(self, address):
        self.calls.append(('type', address))
        return SimpleNamespace(name=self.types[address])

    def string_map(self, address):
        self.calls.append(('string_map', address))
        return {'Recruit': True, 'private_user_name': 'should not export'}

    def dynamic(self, address):
        raise AssertionError('Pinned growth fields are f64/i32/bool, not nullable boxed scalars')


def capture(h, **kwargs):
    params = {'owned_building_pointers': [('hall', 1), ('house', 1)],
              'identity': IDENTITY, 'profile': PROFILE}
    params.update(kwargs)
    with patch.object(module, 'string', side_effect=lambda h, value: value):
        return module.capture_growth(h, 'me', 'state', **params)


class GrowthCaptureTests(unittest.TestCase):
    def test_native_progress_and_happiness_without_invented_bonus(self):
        value = capture(FakeH())
        self.assertEqual(value['status'], 'partial')
        self.assertEqual(value['town_halls'][0]['growth_progress'], 33.)
        self.assertEqual(value['native_inputs']['current_happiness'], 4.)
        self.assertIsNone(value['growth_bonus'])
        self.assertEqual(value['bonus_inputs']['normal_map_tech_presence'],
                         {'Recruit': True, 'EndlessTide': False, 'PlotThrone': False})
        self.assertNotIn('private_user_name', str(value))

    def test_build_and_data_fail_before_any_native_reads(self):
        for key in PROFILE:
            h = FakeH()
            with self.assertRaises(ValueError):
                capture(h, profile={**PROFILE, key: 'bad'})
            self.assertEqual(h.calls, [])

    def test_complete_identity_required(self):
        h = FakeH()
        with self.assertRaises(ValueError):
            capture(h, identity={})
        self.assertEqual(h.calls, [])

    def test_foreign_or_ai_cannot_be_captured(self):
        for key, value in [(('me', 'aiJob'), 'ai'), (('state', 'me'), 'other'),
                           (('res', 'entity'), 'other'), (('hall', 'player'), 'other'),
                           (('z1', 'owner'), 'other')]:
            h = FakeH()
            h.fields[key] = value
            with self.assertRaises(ValueError):
                capture(h)

    def test_only_owned_hall_fields_and_growth_modifier_bit_are_used(self):
        h = FakeH()
        h.fields['flags', 'value'] = 32
        value = capture(h)
        self.assertTrue(value['native_inputs']['growth_disabled_modifier'])
        self.assertFalse(any(c[0] == 'field' and c[1] == 'house' and c[2] != 'zone' for c in h.calls))
        self.assertFalse(any(c[0] == 'field' and c[2] in ('foes', 'name', 'uid', 'players') for c in h.calls))

    def test_optional_missing_fields_remain_null(self):
        h = FakeH()
        del h.fields['res', 'currentHappiness']
        del h.fields['tech', 'researchedTechs']
        value = capture(h)
        self.assertIsNone(value['native_inputs']['current_happiness'])
        self.assertIsNone(value['bonus_inputs']['normal_map_tech_presence'])
        self.assertIn('growth_optional_native_field_unavailable', value['reason_codes'])

    def test_native_progress_change_marks_read_race(self):
        h = FakeH()
        real = h.field
        reads = 0
        def changing(address, field):
            nonlocal reads
            value = real(address, field)
            if (address, field) == ('hall', 'growth'):
                reads += 1
                return value + (1 if reads > 1 else 0)
            return value
        h.field = changing
        value = capture(h)
        self.assertEqual(value['status'], 'read_race')
        self.assertEqual(value['read_guard_issues'], ['town_hall_growth_changed_during_read'])

    def test_nonnullable_native_values_do_not_use_dynamic_unboxing(self):
        # Verified metadata: growth/currentHappiness/recruitStack/timers=f64,
        # population/nbMaxPop/nbReservePop=i32, and gates=bool. FakeH.dynamic
        # raises, so this exercises direct scalar reads rather than pretending
        # a nullable<f64> pointer is itself a progress value.
        h = FakeH()
        h.fields['hall', 'growth'] = 77.25
        h.fields['res', 'currentHappiness'] = -0.5
        value = capture(h)
        self.assertEqual(value['town_halls'][0]['growth_progress'], 77.25)
        self.assertEqual(value['native_inputs']['current_happiness'], -0.5)


if __name__ == '__main__':
    unittest.main()
