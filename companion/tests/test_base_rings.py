import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from base_rings import (BaseRingTracker, derive_base_neighborhood, MAP_SOURCE,
                        HLBOOT_SHA256, DATA_CDB_SHA256)


def observation(*, when=10, session='session', owned=None, edges=None):
    if edges is None:
        edges = {1: [2, 3], 2: [1, 4], 3: [1, 4], 4: [2, 3, 5], 5: [4], 6: []}
    identities = {'session_key': session, 'capture_run_id': 'run',
                  'capture_segment_id': 'run:1'}
    own = [1] if owned is None else owned
    known = sorted(edges)
    return {
        'schema_version': 1, 'observation_type': 'PlayerObservation',
        'policy_version': 'player-observation.v1', 'status': 'ok',
        'scope': 'local_player_and_owned_zones',
        'quality': {'identity_verified': True, 'current_visibility_verified': False},
        'local_player_entity_uid': 31, 'game_time_seconds': when, **identities,
        'owned_zone_ids': list(own), 'discovered_zone_ids': known,
        'units': [{'uid': 10, 'kind': 'Scout', 'zone_id': 2}],
        'buildings': [{'uid': 20, 'kind': 'TownHall', 'zone_id': own[0],
                       'is_construction': False}],
        'map_observation': {
            'schema_version': 1, 'policy_version': 'player-map.v1', 'status': 'ok',
            'source': MAP_SOURCE, 'scope': 'local_player_discovered_static_cells',
            'adjacency_semantics': 'geometric_map_cell_neighbors_not_traversability',
            'hlboot_sha256': HLBOOT_SHA256, 'data_cdb_sha256': DATA_CDB_SHA256,
            'map_identity': {'map_instance_id': session, 'biome': 'Steppe'},
            'local_player_entity_uid': 31, 'game_time_seconds': when, **identities,
            'known_zones': [{'id': zid, 'known_geometric_neighbor_ids': list(neighbors)}
                            for zid, neighbors in edges.items()]}}


def at(value, when, **extra):
    output = copy.deepcopy(value)
    output['game_time_seconds'] = when
    output['map_observation']['game_time_seconds'] = when
    output.update(extra)
    return output


class BaseRingTests(unittest.TestCase):
    def test_known_geometric_bfs_with_cycles_and_witness_paths(self):
        result = derive_base_neighborhood(observation())
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['coverage'], 'incomplete')
        self.assertEqual(result['ring0_zone_ids'], [1])
        self.assertEqual(result['ring1_zone_ids'], [2, 3])
        self.assertEqual(result['ring2_zone_ids'], [4])
        self.assertEqual(result['known_unclassified_zone_ids'], [5, 6])
        rows = {row['zone_id']: row for row in result['zones']}
        self.assertEqual(rows[4]['witness_path_zone_ids'], [1, 2, 4])
        self.assertIsNone(rows[5]['observed_graph_distance'])
        self.assertEqual(rows[5]['witness_path_zone_ids'], [])
        self.assertEqual(rows[2]['own_unit_uids'], [10])
        self.assertEqual(rows[1]['own_building_uids'], [20])
        self.assertEqual(rows[1]['ownership'], 'self')
        self.assertEqual(rows[2]['ownership'], 'unknown')
        self.assertTrue(all(row['current_visibility'] == 'unverified' for row in result['zones']))

    def test_geometric_edges_do_not_use_distance_or_traversability(self):
        value = observation()
        for row in value['map_observation']['known_zones']:
            row.update(position={'x': 999999 if row['id'] == 2 else 0, 'y': 0},
                       terrain_kind='Water', traversable=False)
        result = derive_base_neighborhood(value)
        self.assertEqual(result['ring1_zone_ids'], [2, 3])
        self.assertNotIn('traversable', json.dumps(result))

    def test_unknown_shortcut_cannot_be_called_distance_three_or_more(self):
        # An unobserved cell might connect 1 and 5 in two hops. Its identifier
        # and hypothetical connections never appear in the current known graph.
        result = derive_base_neighborhood(observation())
        self.assertIn(5, result['known_unclassified_zone_ids'])
        self.assertNotIn('ring3_zone_ids', result)
        later = observation(edges={1: [2, 3, 7], 2: [1, 4], 3: [1, 4],
                                   4: [2, 3, 5], 5: [4, 7], 6: [], 7: [1, 5]})
        newer = derive_base_neighborhood(later)
        self.assertEqual(newer['ring2_zone_ids'], [4, 5])
        self.assertEqual(next(row for row in newer['zones'] if row['zone_id'] == 5)
                         ['witness_path_zone_ids'], [1, 7, 5])

    def test_missing_map_or_hall_keeps_empty_rings_unknown(self):
        for field in ('map_observation', 'buildings'):
            value = observation()
            del value[field]
            result = derive_base_neighborhood(value)
            self.assertEqual(result['status'], 'unknown')
            self.assertEqual(result['ring0_zone_ids'], [])
            self.assertEqual(result['ring1_zone_ids'], [])
            self.assertEqual(result['ring2_zone_ids'], [])
            self.assertEqual(result['coverage'], 'incomplete')
            self.assertEqual(result['coverage_semantics'],
                             'empty_rings_mean_not_observed_not_absent')

    def test_empty_valid_known_map_is_unknown_and_not_full_coverage(self):
        value = observation()
        value['map_observation']['known_zones'] = []
        result = derive_base_neighborhood(value)
        self.assertEqual(result['status'], 'unknown')
        self.assertIsNone(result['anchor']['zone_id'])
        self.assertIn('known_map_rows_incomplete', result['reason_codes'])

    def test_anchor_requires_unique_explicitly_completed_own_town_hall(self):
        for completion in (True, None, 0, 'false'):
            value = observation()
            value['buildings'][0]['is_construction'] = completion
            self.assertIsNone(derive_base_neighborhood(value)['anchor'])
        value = observation(owned=[1, 2])
        value['buildings'].append({'uid': 21, 'kind': 'TownHall', 'zone_id': 2,
                                   'is_construction': False})
        result = derive_base_neighborhood(value)
        self.assertIsNone(result['anchor'])
        self.assertIn('own_completed_town_hall_ambiguous', result['reason_codes'])
        value['buildings'][1]['is_construction'] = True
        self.assertEqual(derive_base_neighborhood(value)['anchor']['zone_id'], 1)

    def test_reciprocity_missing_rows_and_malformed_edges_are_explicit(self):
        value = observation()
        nodes = value['map_observation']['known_zones']
        nodes[1]['known_geometric_neighbor_ids'] = []  # 1->2 lacks 2->1.
        nodes[0]['known_geometric_neighbor_ids'] += [3, 1, 999999, True, {}, -1]
        result = derive_base_neighborhood(value)
        self.assertEqual(result['ring1_zone_ids'], [3])
        self.assertEqual(result['ring2_zone_ids'], [4])
        self.assertIn(2, result['known_unclassified_zone_ids'])
        self.assertIn('asymmetric_geometric_edges_omitted', result['reason_codes'])
        self.assertIn('invalid_geometric_edges_omitted', result['reason_codes'])
        self.assertIn('duplicate_geometric_edges_deduplicated', result['reason_codes'])
        self.assertNotIn('999999', json.dumps(result))
        nodes[0]['known_geometric_neighbor_ids'] = None
        self.assertEqual(derive_base_neighborhood(value)['ring1_zone_ids'], [])

    def test_duplicate_node_is_not_resolved_by_overwriting(self):
        value = observation()
        value['map_observation']['known_zones'].append(
            {'id': 1, 'known_geometric_neighbor_ids': [6]})
        result = derive_base_neighborhood(value)
        self.assertIsNone(result['anchor']['zone_id'])
        self.assertEqual(result['ring0_zone_ids'], [])
        self.assertNotIn(1, [row['zone_id'] for row in result['zones']])
        self.assertIn('duplicate_map_zone_omitted', result['reason_codes'])

    def test_map_cannot_expand_parent_knowledge_or_reuse_foreign_identity(self):
        value = observation()
        value['map_observation']['known_zones'].append(
            {'id': 444444, 'known_geometric_neighbor_ids': [1], 'secret': 'hidden'})
        encoded = json.dumps(derive_base_neighborhood(value))
        self.assertNotIn('444444', encoded)
        self.assertNotIn('hidden', encoded)
        for key, bad in (('local_player_entity_uid', 32), ('session_key', 'other'),
                         ('capture_run_id', 'other'), ('capture_segment_id', 'other'),
                         ('hlboot_sha256', 'other'), ('data_cdb_sha256', 'other'),
                         ('policy_version', 'other'), ('adjacency_semantics', 'traversable'),
                         ('game_time_seconds', 0)):
            other = observation()
            other['map_observation'][key] = bad
            output = derive_base_neighborhood(other)
            self.assertEqual(output['status'], 'unknown', key)
            self.assertEqual(output['zones'], [], key)

    def test_own_references_whitelist_never_forwards_foreign_dynamic_entities(self):
        value = observation(owned=[1, 3])
        value['units'] += [{'uid': 12, 'kind': 'Warrior', 'zone_id': 2, 'owner_uid': 99},
                           {'uid': 13, 'kind': 'Scout', 'zone_id': 444444},
                           {'uid': 14, 'kind': 'Villager', 'zone_id': None}]
        value['buildings'] += [{'uid': 21, 'kind': 'House', 'zone_id': 3},
                               {'uid': 22, 'kind': 'SecretEnemyBuilding', 'zone_id': 2}]
        value['map_observation']['known_zones'][1].update(
            owner_uid=99, enemy_units=[{'uid': 555555}], remaining_minerals=888888)
        value['buildings'][0]['process_pointer'] = 777777
        result = derive_base_neighborhood(value)
        rows = {row['zone_id']: row for row in result['zones']}
        self.assertEqual(rows[2]['own_unit_uids'], [10])
        self.assertEqual(rows[3]['own_building_uids'], [21])
        encoded = json.dumps(result)
        for banned in ('SecretEnemyBuilding', '555555', '888888', '777777',
                       'process_pointer', 'owner_uid', 'remaining_minerals', '444444'):
            self.assertNotIn(banned, encoded)

    def test_duplicate_own_uid_is_omitted_from_all_entity_references(self):
        value = observation()
        value['units'].append({'uid': 10, 'kind': 'Villager', 'zone_id': 1})
        result = derive_base_neighborhood(value)
        self.assertTrue(all(row['own_unit_uids'] == [] for row in result['zones']))
        self.assertIn('duplicate_own_entity_uid_omitted', result['reason_codes'])

    def test_unverified_parent_has_no_identifiers_or_untrusted_error_text(self):
        values = [None, [], {'status': 'read_error', 'error': 'private/file'}]
        for key, bad in (('status', 'read_error'), ('schema_version', True),
                         ('policy_version', 'other'), ('local_player_entity_uid', True),
                         ('quality', {}), ('game_time_seconds', float('nan')),
                         ('discovered_zone_ids', [1, True]), ('capture_run_id', 'private/file')):
            value = observation()
            value[key] = bad
            values.append(value)
        for value in values:
            result = derive_base_neighborhood(value)
            self.assertEqual(result['status'], 'unknown')
            self.assertNotIn('session_key', result)
            self.assertNotIn('local_player_entity_uid', result)
            self.assertEqual(result['zones'], [])
            self.assertNotIn('private', json.dumps(result))

    def test_pure_output_and_tracker_state_do_not_alias_source_or_prior_output(self):
        value = observation()
        before = copy.deepcopy(value)
        pure = derive_base_neighborhood(value)
        self.assertEqual(value, before)
        value['map_observation']['known_zones'][0]['known_geometric_neighbor_ids'].clear()
        self.assertEqual(pure['ring1_zone_ids'], [2, 3])
        tracker = BaseRingTracker()
        output = tracker.update(before)
        output['anchor']['zone_id'] = 6
        output['zones'][0]['own_building_uids'].append(999)
        next_output = tracker.update(at(before, 11))
        self.assertEqual(next_output['anchor']['zone_id'], 1)
        self.assertEqual(next_output['zones'][0]['own_building_uids'], [20])

    def test_anchor_fixed_after_colonization_loss_replacement_and_migration(self):
        tracker = BaseRingTracker()
        first = observation()
        result = tracker.update(first)
        self.assertEqual(result['anchor']['selection'], 'first_observed_own_town_hall')
        self.assertEqual(result['anchor']['original_spawn_status'], 'unverified')
        multi = at(first, 11, owned_zone_ids=[1, 2])
        multi['buildings'].append({'uid': 21, 'kind': 'TownHall', 'zone_id': 2,
                                   'is_construction': False})
        self.assertEqual(tracker.update(multi)['anchor']['zone_id'], 1)
        replacement = at(multi, 12, owned_zone_ids=[2])
        replacement['buildings'] = [replacement['buildings'][1]]
        output = tracker.update(replacement)
        self.assertEqual(output['anchor']['zone_id'], 1)
        self.assertIsNone(output['anchor']['town_hall_uid'])
        self.assertEqual(output['ring0_zone_ids'], [1])
        migration = at(replacement, 13)
        migration['buildings'][0]['uid'] = 20
        self.assertEqual(tracker.update(migration)['anchor']['zone_id'], 1)

    def test_initial_ambiguity_waits_for_first_unique_completed_hall(self):
        tracker = BaseRingTracker()
        value = observation(owned=[1, 2])
        value['buildings'].append({'uid': 21, 'kind': 'TownHall', 'zone_id': 2,
                                   'is_construction': False})
        self.assertIsNone(tracker.update(value)['anchor'])
        unique = at(value, 11)
        unique['buildings'] = [unique['buildings'][1]]
        output = tracker.update(unique)
        self.assertEqual(output['anchor']['zone_id'], 2)
        self.assertEqual(output['anchor']['first_observed_game_time_seconds'], 11)

    def test_new_session_and_changed_local_identity_reset_anchor(self):
        tracker = BaseRingTracker()
        tracker.update(observation())
        second = observation(when=0, session='next', owned=[2])
        output = tracker.update(second)
        self.assertEqual(output['anchor']['zone_id'], 2)
        self.assertEqual(output['anchor']['first_observed_game_time_seconds'], 0)
        third = observation(when=1, session='next', owned=[3])
        third['local_player_entity_uid'] = 32
        third['map_observation']['local_player_entity_uid'] = 32
        self.assertEqual(tracker.update(third)['anchor']['zone_id'], 3)

    def test_error_gap_pause_rollback_and_capture_boundary_use_only_current_graph(self):
        tracker = BaseRingTracker()
        original = observation()
        tracker.update(original)
        pause = tracker.update(original)
        self.assertEqual(pause['continuity'], 'paused_clock_current_sample_only')
        self.assertNotIn('events', pause)
        self.assertEqual(tracker.update({'status': 'read_error'})['zones'], [])
        disconnected = at(original, 11)
        for row in disconnected['map_observation']['known_zones']:
            row['known_geometric_neighbor_ids'] = []
        output = tracker.update(disconnected)
        self.assertEqual(output['continuity'], 'baseline_after_read_error')
        self.assertEqual(output['ring1_zone_ids'], [])
        gap = tracker.update(at(disconnected, 20))
        self.assertEqual(gap['continuity'], 'sample_gap_current_sample_only')
        rollback = tracker.update(at(disconnected, 15))
        self.assertEqual(rollback['continuity'], 'clock_rollback_current_sample_only')
        boundary = at(original, 16, capture_segment_id='run:2')
        boundary['map_observation']['capture_segment_id'] = 'run:2'
        output = tracker.update(boundary)
        self.assertEqual(output['continuity'], 'capture_boundary_current_sample_only')
        self.assertEqual(output['anchor']['first_observed_game_time_seconds'], 10)

    def test_clock_before_anchor_does_not_backdate_base_or_select_replacement(self):
        tracker = BaseRingTracker()
        value = observation(when=100)
        tracker.update(value)
        before_anchor = at(value, 99)
        before_anchor['owned_zone_ids'] = [2]
        before_anchor['buildings'][0]['zone_id'] = 2
        result = tracker.update(before_anchor)
        self.assertEqual(result['status'], 'unknown')
        self.assertIsNone(result['anchor'])
        self.assertEqual(result['ring0_zone_ids'], [])
        result = tracker.update(at(before_anchor, 101))
        self.assertEqual(result['anchor']['zone_id'], 1)
        self.assertIsNone(result['anchor']['town_hall_uid'])

    def test_missing_current_anchor_zone_does_not_emit_stale_zone_reference(self):
        tracker = BaseRingTracker()
        tracker.update(observation())
        value = observation(when=11, owned=[2], edges={2: [], 3: []})
        value['buildings'][0]['uid'] = 21
        result = tracker.update(value)
        self.assertIsNone(result['anchor']['zone_id'])
        self.assertIsNone(result['anchor']['town_hall_uid'])
        self.assertEqual(result['ring0_zone_ids'], [])
        self.assertEqual(result['known_unclassified_zone_ids'], [2, 3])

    def test_reset_and_constructor_guards(self):
        for bad in (0, -1, True, '5', float('inf'), float('nan'), 10**1000):
            with self.assertRaises(ValueError):
                BaseRingTracker(bad)
        tracker = BaseRingTracker()
        tracker.update(observation())
        tracker.reset()
        output = tracker.update(observation(owned=[2]))
        self.assertEqual(output['anchor']['zone_id'], 2)


if __name__ == '__main__':
    unittest.main()
