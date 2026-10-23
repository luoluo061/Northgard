from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from situation_timeline import staffing, SituationTracker


def observation(t=1):
    return {'observation_type': 'PlayerObservation', 'status': 'ok',
            'quality': {'identity_verified': True}, 'session_key': 's', 'capture_run_id': 'r',
            'capture_segment_id': 'r:1', 'local_player_entity_uid': 31,
            'game_time_seconds': t, 'population': 4, 'population_cap': 6,
            'stocks': {'Food': 100}, 'in_winter': False,
            'buildings': [{'uid': 10, 'kind': 'WoodLodge', 'zone_id': 3, 'assigned_unit_ids': [1, 2]}],
            'units': [{'uid': 1, 'kind': 'Woodcutter', 'zone_id': 3,
                       'assign_to': {'uid': 10}, 'going_to': None,
                       'cur_job': {'kind': 'Active'}, 'produce': 'Wood', 'enabled': True,
                       'has_no_work': False, 'hit_life': 50},
                      {'uid': 2, 'kind': 'Woodcutter', 'zone_id': 4,
                       'assign_to': {'uid': 10}, 'going_to': {'zone_id': 3},
                       'cur_job': {'kind': 'Active'}, 'produce': 'Wood', 'enabled': True,
                       'has_no_work': False, 'hit_life': 50}]}


class SituationTests(unittest.TestCase):
    def test_assignment_does_not_count_travelling_worker_as_active_at_workplace(self):
        counts = staffing(observation())['buildings'][0]
        self.assertEqual(counts['corroborated_assigned_count'], 2)
        self.assertEqual(counts['assigned_in_target_zone_count'], 1)
        self.assertEqual(counts['assigned_with_target_destination_count'], 1)
        self.assertEqual(counts['active_resource_state_candidate_count'], 1)

    def test_disagreement_and_unavailable_location_are_preserved(self):
        row = observation()
        row['units'][1]['assign_to'] = None
        counts = staffing(row)['buildings'][0]
        self.assertFalse(counts['assignment_lists_agree'])
        self.assertEqual(counts['corroborated_assigned_count'], 1)

    def test_arrival_condition_and_health_change_are_separate_facts(self):
        tracker = SituationTracker()
        a, b = observation(), observation(2)
        tracker.feed(a)
        b['units'][1]['zone_id'] = 3
        b['units'][1]['going_to'] = None
        b['units'][0]['hit_life'] = 45
        b['population'] = 6
        result = tracker.feed(b)
        kinds = [e['kind'] for e in result['events']]
        self.assertIn('building_staffing_state_changed', kinds)
        self.assertIn('condition_started_observed', kinds)
        health = next(e for e in result['events'] if e['kind'] == 'unit_health_change_observed')
        self.assertEqual(health['cause'], 'unknown')

    def test_missing_unit_is_not_called_dead_and_gap_resets_baseline(self):
        tracker = SituationTracker()
        tracker.feed(observation())
        b = observation(2)
        b['units'].pop()
        self.assertFalse(any('dead' in e['kind'] for e in tracker.feed(b)['events']))
        self.assertEqual(tracker.feed({'status': 'read_error'})['record_kind'], 'situation_gap')
        self.assertEqual(tracker.feed(observation(3))['record_kind'], 'situation_baseline')
        c = observation(4)
        c['capture_segment_id'] = 'r:2'
        self.assertEqual(tracker.feed(c)['record_kind'], 'situation_baseline')

    def test_input_is_not_mutated(self):
        row = observation()
        saved = deepcopy(row)
        SituationTracker().feed(row)
        self.assertEqual(row, saved)


if __name__ == '__main__':
    unittest.main()
