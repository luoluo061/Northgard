"""Invented roster heaps only; no live game or process attachment."""
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
import match_context as mc
from match_context_projection import project_match_context
from player_observation import project_player_observation

PROFILE = {'hlboot_sha256': mc.HLBOOT_SHA256, 'data_cdb_sha256': mc.DATA_CDB_SHA256}
IDENTITY = {'session_key': 'invented-session', 'capture_run_id': 'invented-run', 'capture_segment_id': 'invented-run:1'}


class Heap:
    def __init__(self, me=10):
        self.me = me
        self.values = {1: {'me': me, 'time': 50., 'players': 100, 'startPlayers': 200,
                          'mode': 300, 'gameType': 302}}
        self.rosters = {100: list(range(10, 16)), 200: list(range(10, 16))}
        for p in range(10, 16):
            self.values[p] = {'teamLeader': 10 if p < 13 else 13}
        self.values[me].update(__uid=31, aiJob=0)
        self.enums = {300: ('Multiplayer', [(923, 301)]), 301: ('Team3V3', []), 302: ('Private', [])}
        self.names = {1: 'GameState', **{p: 'ent.Player' for p in range(10, 16)},
                      300: 'enum:PlayMode', 301: 'enum:MultiMode', 302: 'enum:GameType'}
        self.reads = []
    def field(self, pointer, field):
        self.reads.append((pointer, field))
        return self.values[pointer][field]
    def object_type(self, pointer):
        return SimpleNamespace(name=self.names.get(pointer, 'unknown'), kind=18 if pointer >= 300 else 11)
    def type_info(self, pointer):
        return SimpleNamespace(name='enum:MultiMode' if pointer == 923 else 'unknown')


def capture(heap=None, **kwargs):
    heap = heap or Heap()
    with patch.object(mc, 'objects', side_effect=lambda h,p,limit=64:list(h.rosters[p])), \
            patch.object(mc, 'enum_values', side_effect=lambda h,p:deepcopy(h.enums[p])):
        return mc.capture_match_context(heap, heap.me, 1, profile=kwargs.get('profile', PROFILE), identity=IDENTITY)


def parent():
    return {'schema_version': 2, 'status': 'ok', **IDENTITY, 'game_time_seconds': 50.,
        'local_player_entity_uid': 31, 'clan': 'Bear',
        'local_player_identity': {'verified': True, 'role': 'player', 'entity_uid': 31,
                                  'is_ai': False, 'in_player_list': True},
        'game_context': {'mode': {'kind': 'Multiplayer', 'args': [{'kind': 'Team3V3', 'args': []}]},
                         'game_type': {'kind': 'Private', 'args': []},
                         'bifrost_data_present': False, 'editor_present': False},
        'stocks': {key: 100. for key in ('Food', 'Wood', 'Money', 'Stone', 'Iron', 'Lore')},
        'population': 1, 'population_cap': 6, 'in_winter': False,
        'owned_zone_ids': [7], 'discovered_zone_ids': [7],
        'units': [{'uid': 601, 'kind': 'Villager', 'zone_id': 7}],
        'buildings': [{'uid': 701, 'kind': 'TownHall', 'zone_id': 7}]}


class MatchContextCaptureTests(unittest.TestCase):
    def test_three_v_three_roster_and_mode_both_verified(self):
        h = Heap()
        row = capture(h)
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['native_mode_kind'], 'Multiplayer')
        self.assertEqual(row['native_mode_variant'], 'Team3V3')
        self.assertEqual(row['native_player_count'], 6)
        self.assertEqual(row['native_current_player_count'], 6)
        self.assertEqual(row['native_team_count'], 2)
        self.assertEqual(row['native_own_team_size'], 3)
        self.assertEqual(row['native_other_team_sizes'], [3])
        self.assertEqual(row['native_local_team_id'], 0)
        self.assertEqual(row['team_layout'], '3v3')
        self.assertTrue(row['configured_layout_matches_roster'])
        self.assertEqual(row['player_count_semantics'], 'start_roster_slots_including_ai_if_present')
        for pointer, field in h.reads:
            if pointer in range(11, 16):
                self.assertEqual(field, 'teamLeader')
    def test_local_team_ordinal_depends_on_group_order_not_account(self):
        row = capture(Heap(me=14))
        self.assertEqual(row['native_local_team_id'], 1)
        self.assertEqual(row['native_own_team_size'], 3)
        self.assertNotIn('players', row)
        self.assertNotIn('teamLeader', row)
    def test_mode_declaration_does_not_invent_roster(self):
        h = Heap(); h.values[1]['startPlayers'] = 0
        row = capture(h)
        self.assertEqual(row['configured_team_layout'], '3v3')
        self.assertIsNone(row['native_player_count'])
        self.assertIsNone(row['team_layout'])
        self.assertEqual(row['status'], 'partial')
    def test_current_roster_is_distinct_from_starting_slots(self):
        h = Heap(); h.rosters[100] = [10, 11, 12, 13, 14]
        row = capture(h)
        self.assertEqual(row['native_current_player_count'], 5)
        self.assertEqual(row['native_player_count'], 6)
        self.assertEqual(row['team_layout'], '3v3')
    def test_free_teams_can_use_observed_roster_without_assuming_count(self):
        h = Heap(); h.enums[301] = ('FreeTeams', [])
        row = capture(h)
        self.assertIsNone(row['configured_team_layout'])
        self.assertIsNone(row['configured_layout_matches_roster'])
        self.assertEqual(row['team_layout'], '3v3')
    def test_missing_or_external_leader_is_unknown(self):
        for value in (987654321, 11):
            h = Heap(); h.values[10]['teamLeader'] = value
            row = capture(h)
            self.assertIsNone(row['native_own_team_size'])
            self.assertIsNone(row['team_layout'])
            self.assertNotIn('987654321', json.dumps(row))
    def test_multi_member_null_leader_is_not_guessed(self):
        h = Heap(); h.values[10]['teamLeader'] = 0
        self.assertIsNone(capture(h)['team_layout'])
    def test_stable_solo_null_leaders_follow_native_get_team(self):
        h = Heap(); h.enums[301] = ('FreeForAll', [])
        for p in range(10, 16): h.values[p]['teamLeader'] = 0
        row = capture(h)
        self.assertEqual(row['native_team_count'], 6)
        self.assertEqual(row['native_own_team_size'], 1)
        self.assertEqual(row['team_layout'], '1v1v1v1v1v1')
    def test_declared_3v3_conflicting_with_observed_roster_not_verified(self):
        h = Heap(); h.values[12]['teamLeader'] = 13
        row = capture(h)
        self.assertEqual(row['native_player_count'], 6)
        self.assertEqual(row['native_own_team_size'], 2)
        self.assertFalse(row['configured_layout_matches_roster'])
        self.assertIsNone(row['team_layout'])
    def test_duplicate_roster_or_non_player_type_is_unknown(self):
        for variant in ('duplicate', 'foreign_type'):
            h = Heap()
            if variant == 'duplicate': h.rosters[200].append(15)
            else: h.names[15] = 'ent.Unit'
            row = capture(h)
            self.assertIsNone(row['team_layout'])
    def test_team_mutation_during_read_is_a_race(self):
        h = Heap(); original = h.field; reads = [0]
        def field(pointer, name):
            if pointer == 12 and name == 'teamLeader':
                reads[0] += 1
                if reads[0] > 1: return 13
            return original(pointer, name)
        h.field = field
        row = capture(h)
        self.assertEqual(row['status'], 'read_race')
        self.assertIsNone(row['team_layout'])
        self.assertEqual(project_match_context(row, parent())['status'], 'rejected')
    def test_game_clock_rollback_and_local_identity_change_fail(self):
        h = Heap(); original = h.field; reads = [0]
        def field(pointer, name):
            if pointer == 1 and name == 'time':
                reads[0] += 1
                return 50. if reads[0] == 1 else 49.
            return original(pointer, name)
        h.field = field
        self.assertEqual(capture(h)['status'], 'read_race')
        h = Heap(); h.values[1]['me'] = 11
        self.assertEqual(capture(h)['status'], 'unavailable')
    def test_no_ai_local_player_or_unverified_build(self):
        h = Heap(); h.values[10]['aiJob'] = 999
        self.assertEqual(capture(h)['status'], 'unavailable')
        self.assertEqual(capture(profile={})['status'], 'unavailable')
    def test_pause_is_zero_elapsed_game_time(self):
        row = capture()
        self.assertEqual(row['time_before'], row['time_after'])
        self.assertEqual(row['sample_span_game_seconds'], 0.)
    def test_unavailable_mode_does_not_fabricate_mode_from_counts(self):
        h = Heap(); h.names[300] = 'unverified'
        row = capture(h)
        self.assertIsNone(row['native_mode_kind'])
        self.assertEqual(row['native_player_count'], 6)
        self.assertEqual(row['team_layout'], '3v3')


class MatchContextProjectionTests(unittest.TestCase):
    def test_pointer_free_counts_survive(self):
        row = project_match_context(capture(), parent())
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['team_layout'], '3v3')
    def test_native_layout_is_recomputed_not_copied(self):
        raw = capture(); raw['team_layout'] = '4v4'; raw['configured_team_layout'] = '4v4'
        row = project_match_context(raw, parent())
        self.assertEqual(row['team_layout'], '3v3')
        self.assertEqual(row['configured_team_layout'], '3v3')
    def test_unknown_roster_does_not_promote_configured_format(self):
        h = Heap(); h.values[1]['startPlayers'] = 0
        row = project_match_context(capture(h), parent())
        self.assertEqual(row['configured_team_layout'], '3v3')
        self.assertIsNone(row['team_layout'])
    def test_invalid_counts_identity_or_enum_rejected(self):
        for field, value in [('native_player_count', True), ('native_own_team_size', 99),
                             ('native_other_team_sizes', [2]), ('native_local_team_id', 2),
                             ('native_current_player_count', None), ('native_current_player_count', False),
                             ('session_key', 'other'), ('native_mode_kind', {'name': 'Multiplayer'})]:
            raw = capture(); raw[field] = value
            self.assertEqual(project_match_context(raw, parent())['status'], 'rejected', field)
    def test_missing_native_enum_stays_partial_even_if_raw_diagnostics_omitted(self):
        raw = capture(); raw['native_mode_kind'] = raw['native_mode_variant'] = raw['game_type_kind'] = None
        raw['unknown_fields'] = raw['reason_codes'] = []
        row = project_match_context(raw, parent())
        self.assertEqual(row['status'], 'partial')
        self.assertEqual(row['team_layout'], '3v3')
        self.assertIn('native_mode_kind', row['unknown_fields'])
        self.assertIn('game_type_kind', row['unknown_fields'])
        raw = capture(); raw['native_mode_variant'] = None
        self.assertEqual(project_match_context(raw, parent())['status'], 'rejected')
    def test_parent_native_mode_must_agree(self):
        p = parent(); p['game_context']['mode']['args'][0]['kind'] = 'Team2V2'
        self.assertEqual(project_match_context(capture(), p)['status'], 'rejected')
    def test_no_names_accounts_roster_rows_or_unrecognized_diagnostics(self):
        raw = capture(); raw['players'] = [{'name': 'private', 'account': 'private'}]
        raw['raw_leader_pointer'] = 987654321
        row = project_match_context(raw, parent())
        self.assertNotIn('private', json.dumps(row))
        self.assertNotIn('987654321', json.dumps(row))
        raw['reason_codes'] = ['private path/account']
        self.assertNotIn('private path', json.dumps(project_match_context(raw, parent())))
    def test_optional_component_failure_preserves_basic_player_observation(self):
        p = parent(); p['match_context'] = {'status': 'unavailable'}
        row = project_player_observation(p)
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['stocks']['Food'], 100.)
        self.assertEqual(row['match_context']['status'], 'rejected')
    def test_optional_capture_wrapper_drops_private_exception_text(self):
        import action_capture
        with patch.object(action_capture, 'capture_match_context', side_effect=OSError('C:/private/name/0x1234')):
            row = action_capture.capture_match_context_component(None, 1, 2, profile=PROFILE, identity=IDENTITY)
        self.assertEqual(row['status'], 'unavailable')
        self.assertNotIn('private', json.dumps(row))


if __name__ == '__main__':
    unittest.main()
