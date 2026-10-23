"""Synthetic read-only heap checks; these are not a live-match calibration."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / 'companion/core'))
import labor_capture as lc
from labor_projection import project_labor

PROFILE = {'hlboot_sha256': lc.VERIFIED_HASH, 'data_cdb_sha256': lc.VERIFIED_CDB_HASH}
IDENTITY = {'session_key': 'test-session', 'capture_run_id': 'test-run', 'capture_segment_id': 'test-run:1'}
RULES = {'schema_version': 1, **PROFILE, 'tables': {
    'unit': [{'id': 'Farmer', 'produce': [{'r': 'Wheat', 'q': .5, 'req': {'buildings': [{'building': 'Fields'}]}}]}],
    'resource': [{'id': 'Wheat', 'props': {'converts': 'Food'}}]}}


class Heap:
    def __init__(self):
        self.values = {
            1: {'__uid': 31, 'aiJob': 0, 'res': 3, 'tech': 4, 'upgrades': 5,
                'clan': 'Bear', 'evars': 6, 'owl': 0},
            2: {'me': 1, 'time': 100., 'inWinter': False},
            3: {'currentHappiness': 2., 'coefCons': 1., 'population': 5,
                'overworking': False, 'lastFeast': -100., 'lastOverwork': -100.},
            4: {'researchedTechs': 101, 'fameLevel': 0, 'allFountainResearches': 0},
            5: {'upgradedUnits': 102}, 6: {'affectsTime': 103},
            11: {'id': 7, 'owner': 1, 'evars': 12, 'flags': 13},
            12: {'affectsTime': 104}, 13: {'value': 0},
            21: {'__uid': 51, 'zone': 11, 'kind': 'Fields', 'upgraded': 1,
                 'extended': False, 'hitLife': 0., 'waitRepair': False, 'evars': 22, 'state': 24},
            22: {'affectsTime': 105}, 24: {'progress': 500, 'contains': 501},
            31: {'__uid': 41, 'owner': 1, 'kind': 'Farmer', 'zone': 11, 'assignTo': 21,
                 'produce': 'Wheat', 'hasNoWork': False, 'curJob': 901, 'justAssigned': False,
                 'enabled': True, 'hitLife': 0., 'sickCounter': 0., 'sickReason': 0,
                 'status': 0, 'statusTimer': 0., 'assignedJob': 'Farmer', 'flags': 33, 'evars': 32},
            32: {'affectsTime': 106}, 33: {'value': 0},
            91: {'__uid': 999, 'owner': 88, 'kind': 'Warrior'},
            92: {'id': 999, 'owner': 88},
            93: {'__uid': 999, 'zone': 92, 'kind': 'SecretRelic'},
        }
        self.types = {1: 'ent.Player', 3: 'ent.player.ResourcesComponent', 4: 'ent.player.TechComponent',
            5: 'ent.player.UpgradesComponent', 6: 'ent.EntityVariables', 11: 'ent.Zone',
            12: 'ent.EntityVariables', 13: 'hxbit.EnumFlagsData', 21: 'ent.Building', 22: 'ent.EntityVariables',
            31: 'ent.Unit', 32: 'ent.EntityVariables', 33: 'hxbit.EnumFlagsData', 92: 'ent.Zone'}
        self.maps = {101: {'Lumber': True}, 102: {'Farmer': 1}, 103: {}, 104: {'Winter': 120.},
                     105: {}, 106: {'Wounded': 112.}}
        self.boxed = {500: .8, 501: 120.}
    def field(self, ptr, name):
        return self.values[ptr][name]
    def object_type(self, ptr):
        if ptr in self.boxed:
            return SimpleNamespace(name='f64', kind=6)
        return SimpleNamespace(name=self.types.get(ptr, 'unknown'), kind=11)
    def string_map(self, ptr, limit=1024):
        return deepcopy(self.maps[ptr])
    def dynamic(self, ptr):
        return self.boxed[ptr]


def capture(heap=None, **kwargs):
    heap = heap or Heap()
    with patch.object(lc, 'string', lambda h,p:p), patch.object(lc, 'objects', lambda h,p,limit=1024:[]), \
            patch.object(lc, 'enum_values', return_value=('Working', [])):
        return lc.capture_labor(heap, 1, 2,
            own_units=kwargs.pop('own_units', [(31, {'uid': 41, 'kind': 'Farmer', 'zone_id': 7})]),
            owned_zones=kwargs.pop('owned_zones', [(11, 7)]),
            owned_buildings=kwargs.pop('owned_buildings', [(21, {'uid': 51, 'kind': 'Fields', 'zone_id': 7})]),
            profile=kwargs.pop('profile', PROFILE), identity=IDENTITY,
            local_rules=kwargs.pop('local_rules', RULES), **kwargs)


def parent():
    return {'status': 'ok', **IDENTITY, 'local_player_entity_uid': 31,
        'local_player_identity': {'verified': True, 'role': 'player', 'entity_uid': 31,
                                  'is_ai': False, 'in_player_list': True},
        'game_time_seconds': 100., 'clan': 'Bear', 'owned_zone_ids': [7], 'discovered_zone_ids': [7],
        'units': [{'uid': 41, 'kind': 'Farmer', 'zone_id': 7}],
        'buildings': [{'uid': 51, 'kind': 'Fields', 'zone_id': 7}]}


class LaborCaptureTests(unittest.TestCase):
    def test_materialized_inputs_and_configuration_are_separate(self):
        row = capture()
        self.assertEqual(row['status'], 'ok')
        self.assertFalse(row['native_individual_rates_available'])
        unit = row['units'][0]
        self.assertIsNone(unit['native_actual_rate'])
        self.assertIsNone(unit['native_settled_amount'])
        self.assertIsNone(unit['model_effective_rate'])
        self.assertIsNone(unit['can_produce'])
        self.assertEqual(unit['configuration_candidates'][0]['configured_base_q'], .5)
        self.assertEqual(unit['configuration_candidates'][0]['requirement_status'], 'not_evaluated')
        self.assertEqual(unit['inputs']['cur_job_kind'], 'Working')
        self.assertEqual(unit['effects'], [{'kind': 'Wounded', 'raw_end_time': 112.}])
        self.assertEqual(row['player_context']['researched_tech_entries'], [{'id': 'Lumber', 'value': True}])
        self.assertEqual(row['buildings'][0]['state_progress'], .8)
        self.assertEqual(unit['assignment']['building_uid'], 51)
        self.assertEqual(row['time_before'], row['time_after'])
    def test_build_and_data_hashes_both_required(self):
        for profile in ({'hlboot_sha256': lc.VERIFIED_HASH}, {'data_cdb_sha256': lc.VERIFIED_CDB_HASH}, {}):
            result = capture(profile=profile)
            self.assertEqual(result['status'], 'unknown')
            self.assertEqual(result['units'], [])
            self.assertIn('labor_unverified_build', result['reason_codes'])
    def test_foreign_objects_never_exported(self):
        row = capture(own_units=[(31, {'uid': 41, 'kind': 'Farmer', 'zone_id': 7}),
                                 (91, {'uid': 999, 'kind': 'Warrior', 'zone_id': 999})],
                      owned_zones=[(11, 7), (92, 999)],
                      owned_buildings=[(21, {'uid': 51, 'kind': 'Fields', 'zone_id': 7}),
                                       (93, {'uid': 999, 'kind': 'SecretRelic', 'zone_id': 999})])
        self.assertEqual(len(row['units']), 1)
        self.assertEqual(len(row['zones']), 1)
        self.assertEqual(len(row['buildings']), 1)
        self.assertNotIn('999', json.dumps(row))
        self.assertNotIn('SecretRelic', json.dumps(row))
    def test_missing_input_is_unknown_preserving_other_parts(self):
        h = Heap(); del h.values[31]['sickCounter']
        result = capture(h)
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['units'][0]['inputs']['sick_counter'])
        self.assertEqual(result['units'][0]['inputs']['produce'], 'Wheat')
    def test_ai_and_changed_local_identity_fail_closed(self):
        for key, value in (('aiJob', 99),):
            h = Heap(); h.values[1][key] = value
            self.assertEqual(capture(h)['units'], [])
        h = Heap(); h.values[2]['me'] = 88
        self.assertEqual(capture(h)['units'], [])
    def test_foreign_assignment_not_followed(self):
        h = Heap(); h.values[31]['assignTo'] = 987654321
        result = capture(h)
        self.assertIsNone(result['units'][0]['assignment'])
        self.assertNotIn('987654321', json.dumps(result))
    def test_non_scalar_maps_do_not_escape(self):
        h = Heap(); h.maps[101] = {'Lumber': {'_address': 987654321, 'secret': 'private-path'}}
        result = capture(h)
        self.assertIsNone(result['player_context']['researched_tech_entries'])
        self.assertNotIn('private-path', json.dumps(result))
        self.assertNotIn('987654321', json.dumps(result))
    def test_unit_transition_during_read_omits_unit(self):
        h = Heap(); old = h.field; reads = [0]
        def field(ptr, name):
            if ptr == 31 and name == 'produce':
                reads[0] += 1
                if reads[0] >= 3: return 'Food'
            return old(ptr, name)
        h.field = field
        self.assertEqual(capture(h)['units'], [])
    def test_time_rollback_discards_all_inputs(self):
        h = Heap(); old = h.field; reads = [0]
        def field(ptr, name):
            if ptr == 2 and name == 'time':
                reads[0] += 1
                return 100. if reads[0] == 1 else 90.
            return old(ptr, name)
        h.field = field
        row = capture(h)
        self.assertEqual(row['status'], 'unknown')
        self.assertEqual(row['units'], [])
        self.assertEqual(row['player_context'], {})
    def test_local_rule_fingerprint_mismatch_withholds_q(self):
        bad = deepcopy(RULES); bad['data_cdb_sha256'] = 'bad'
        row = capture(local_rules=bad)
        self.assertIsNone(row['units'][0]['configuration_candidates'])
        self.assertEqual(row['status'], 'partial')
    def test_raw_parent_zone_must_match_memory(self):
        row = capture(own_units=[(31, {'uid': 41, 'kind': 'Farmer', 'zone_id': 999})])
        self.assertEqual(row['units'], [])
    def test_nullable_progress_unboxes_instead_of_exporting_pointer(self):
        row = capture()
        self.assertEqual(row['buildings'][0]['state_progress'], .8)
        self.assertEqual(row['buildings'][0]['state_contains'], 120.)
        self.assertNotIn('500', json.dumps(row['buildings']))
        self.assertNotIn('501', json.dumps(row['buildings']))
        h = Heap(); h.values[24]['progress'] = 0; h.values[24]['contains'] = 0
        row = capture(h)
        self.assertIsNone(row['buildings'][0]['state_progress'])
        self.assertIsNone(row['buildings'][0]['state_contains'])
        self.assertEqual(row['status'], 'ok')
    def test_unknown_box_never_exports_address_as_progress(self):
        h = Heap(); h.values[24]['progress'] = 987654321
        row = capture(h)
        self.assertIsNone(row['buildings'][0]['state_progress'])
        self.assertEqual(row['status'], 'partial')
        self.assertNotIn('987654321', json.dumps(row))
    def test_malformed_context_pairs_do_not_crash(self):
        self.assertEqual(capture(own_units=[None])['units'], [])
    def test_enum_arguments_not_serialized(self):
        with patch.object(lc, 'enum_values', return_value=('Attack', [('secret-type', {'owner': 'secret'})])):
            # direct helper must retain only constructor label
            self.assertEqual(lc._job_kind(Heap(), 901), 'Attack')


class LaborProjectionTests(unittest.TestCase):
    def test_valid_capture_projects_and_never_claims_actual_output(self):
        row = project_labor(capture(), parent())
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['units'][0]['configuration_candidates'][0]['configured_base_q'], .5)
        self.assertIsNone(row['units'][0]['native_actual_rate'])
        self.assertIsNone(row['units'][0]['can_produce'])
    def test_arbitrary_fields_and_object_values_dropped(self):
        raw = capture(); raw['private'] = 'account-name'
        raw['units'][0]['name'] = 'account-name'
        raw['units'][0]['inputs']['private'] = {'pointer': 987654321}
        raw['units'][0]['native_actual_rate'] = .5
        raw['units'][0]['model_effective_rate'] = 9
        raw['units'][0]['can_produce'] = True
        raw['player_context']['researched_tech_entries'].append({'id': 'Lumber2', 'value': {'secret': 'account-name'}})
        result = project_labor(raw, parent())
        self.assertNotIn('account-name', json.dumps(result))
        self.assertNotIn('987654321', json.dumps(result))
        self.assertIsNone(result['units'][0]['native_actual_rate'])
        self.assertIsNone(result['units'][0]['model_effective_rate'])
        self.assertIsNone(result['units'][0]['can_produce'])
    def test_session_or_identity_mismatch_rejected(self):
        for key in IDENTITY:
            raw = capture(); raw[key] = 'different'
            self.assertEqual(project_labor(raw, parent())['status'], 'rejected')
        p = parent(); p['local_player_identity']['is_ai'] = True
        self.assertEqual(project_labor(capture(), p)['status'], 'rejected')
    def test_clock_span_and_hash_mismatch_rejected(self):
        for field, value in [('time_after', 110.), ('time_before', 101.),
                             ('data_cdb_sha256', 'wrong'), ('sample_span_game_seconds', True)]:
            raw = capture(); raw[field] = value
            self.assertEqual(project_labor(raw, parent())['status'], 'rejected')
    def test_unknown_entity_or_zone_omitted(self):
        raw = capture(); foreign = deepcopy(raw['units'][0]); foreign['uid'] = 999
        raw['units'].append(foreign); raw['zones'].append({'zone_id': 999, 'flags': 1, 'effects': []})
        result = project_labor(raw, parent())
        self.assertNotIn('999', json.dumps(result))
        self.assertEqual(len(result['units']), 1)
        self.assertEqual(len(result['zones']), 1)
    def test_assignment_must_be_own_building(self):
        raw = capture(); raw['units'][0]['assignment'] = {'building_uid': 999, 'zone_id': 999, 'same_zone': True}
        result = project_labor(raw, parent())
        self.assertIsNone(result['units'][0]['assignment'])
        self.assertNotIn('999', json.dumps(result))
    def test_non_finite_and_bool_quantities_are_null(self):
        raw = capture(); raw['units'][0]['inputs']['hit_life'] = float('nan')
        raw['units'][0]['inputs']['sick_counter'] = True
        result = project_labor(raw, parent())
        self.assertIsNone(result['units'][0]['inputs']['hit_life'])
        self.assertIsNone(result['units'][0]['inputs']['sick_counter'])
    def test_diagnostic_text_not_copied(self):
        raw = capture(); raw['reason_codes'].append('private path/account')
        raw['unknown_fields'].append('0xDEADBEEF')
        result = project_labor(raw, parent())
        self.assertNotIn('private path', json.dumps(result))
        self.assertNotIn('DEADBEEF', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
