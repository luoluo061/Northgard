import copy
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('action_diff', Path(__file__).resolve().parents[1] / 'core/action_diff.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def snapshot(time=10, **extra):
    data = {'status': 'ok', 'session_key': 'match-a', 'pid': 12, 'clan': 'Bear',
            'capture_run_id': 'run-a', 'capture_segment_id': 'run-a:1',
            'local_player_entity_uid': 31, 'game_time_seconds': time,
            'stocks': {'Food': 100, 'Wood': 100}, 'units': [], 'buildings': [], 'native_events': []}
    data.update(extra)
    return data


def construction(uid=9, **extra):
    row = {'uid': uid, 'kind': 'Construction', 'is_construction': True,
           'target_kind': 'House', 'zone_id': 82, 'position': {'x': 100, 'y': 120}}
    row.update(extra)
    return row


class ActionDiffTests(unittest.TestCase):
    def events(self, before, after, **kwargs):
        return MODULE.diff_snapshots(before, after, **kwargs)['events']

    def test_disappearance_is_not_cancellation(self):
        events = self.events(snapshot(buildings=[construction()]), snapshot(11))
        self.assertEqual([e['kind'] for e in events], ['construction_removed_unresolved'])

    def test_native_completion_beats_cancel_intent(self):
        native = {'session_key': 'match-a', 'capture_run_id': 'run-a', 'capture_segment_id': 'run-a:1',
                  'index': 7, 'time': 11, 'kind': 'AddBuilding', 'params': [
            {'entity_uid': 31}, {'zone_id': 82}, 'House', {'x': 100, 'y': 120}]}
        intents = [{'session_key': 'match-a', 'capture_run_id': 'run-a', 'capture_segment_id': 'run-a:1',
                    'kind': 'cancel_construction', 'status': 'confirmed', 'game_time_seconds': 10.5, 'target': {'uid': 9}}]
        events = self.events(snapshot(buildings=[construction()]), snapshot(11), native_events=[native], intents=intents)
        self.assertEqual(events[0]['kind'], 'construction_completed')

    def test_confirmed_cancel_needs_target(self):
        intent = {'session_key': 'match-a', 'capture_run_id': 'run-a', 'capture_segment_id': 'run-a:1',
                  'kind': 'cancel_construction', 'status': 'confirmed', 'game_time_seconds': 11, 'target': {'uid': 9}}
        before, after = snapshot(buildings=[construction()]), snapshot(11)
        self.assertEqual(self.events(before, after, intents=[intent])[0]['kind'], 'construction_cancelled')
        intent['target']['uid'] = 88
        self.assertEqual(self.events(before, after, intents=[intent])[0]['kind'], 'construction_removed_unresolved')

    def test_command_is_not_confirmed_success(self):
        intent = {'session_key': 'match-a', 'capture_run_id': 'run-a', 'capture_segment_id': 'run-a:1',
                  'kind': 'cancel_construction', 'status': 'issued', 'game_time_seconds': 11, 'target': {'uid': 9}}
        events = self.events(snapshot(buildings=[construction()]), snapshot(11), intents=[intent])
        self.assertEqual(events[0]['kind'], 'construction_removed_after_cancel_command')
        self.assertEqual(events[0]['confidence']['level'], 'medium')

    def test_completion_entity_correlates_replacement_uid(self):
        building = construction(uid=10, kind='House', is_construction=False)
        events = self.events(snapshot(buildings=[construction()]), snapshot(11, buildings=[building]))
        self.assertEqual([e['kind'] for e in events], ['construction_completed'])
        self.assertEqual(events[0]['resulting_building']['uid'], 10)

    def test_ambiguous_completion_stays_unresolved(self):
        first = construction(uid=10, kind='House', is_construction=False)
        second = construction(uid=11, kind='House', is_construction=False)
        events = self.events(snapshot(buildings=[construction()]), snapshot(11, buildings=[first, second]))
        self.assertEqual(events[0]['kind'], 'construction_removed_unresolved')

    def test_one_result_cannot_complete_two_ambiguous_constructions(self):
        one, two = construction(), construction(uid=12)
        one.pop('position')
        two.pop('position')
        completed = construction(uid=10, kind='House', is_construction=False)
        events = self.events(snapshot(buildings=[one, two]), snapshot(11, buildings=[completed]))
        self.assertEqual(sum(e['kind'] == 'construction_completed' for e in events), 0)
        self.assertEqual(sum(e['kind'] == 'construction_removed_unresolved' for e in events), 2)

    def test_kind_change_and_movement_are_distinct(self):
        unit = {'uid': 3, 'kind': 'Villager', 'zone_id': 82}
        before = snapshot(units=[unit])
        moved = dict(unit, zone_id=85)
        self.assertEqual([e['kind'] for e in self.events(before, snapshot(11, units=[moved]))], ['unit_zone_changed'])
        changed = dict(unit, kind='Lumberjack')
        self.assertEqual([e['kind'] for e in self.events(before, snapshot(11, units=[changed]))], ['unit_kind_changed'])

    def test_assignment_dict_key_order_is_irrelevant(self):
        old = {'uid': 3, 'kind': 'Villager', 'assigned_job': {'kind': 'Build', 'args': [9]}}
        new = dict(old, assigned_job={'args': [9], 'kind': 'Build'})
        self.assertEqual(self.events(snapshot(units=[old]), snapshot(11, units=[new])), [])

    def test_stock_fall_remains_net_observation(self):
        result = MODULE.diff_snapshots(snapshot(), snapshot(11, stocks={'Food': 105, 'Wood': 50}, buildings=[construction()]))
        self.assertEqual(result['resource_changes']['Wood']['net_observed_change'], -50)
        self.assertEqual(result['events'][0]['kind'], 'construction_started_observed')
        self.assertEqual(result['events'][0]['attribution'], 'contemporaneous_only')
        self.assertNotIn('cost', result['events'][0])

    def test_paused_interval_has_no_infinite_rate(self):
        result = MODULE.diff_snapshots(snapshot(), snapshot(stocks={'Food': 100, 'Wood': 50}))
        self.assertTrue(result['time_interval']['paused_or_same_game_time'])
        self.assertIsNone(result['resource_changes']['Wood']['observed_net_rate_per_game_second'])

    def test_session_and_time_rollback_reset_no_cross_diff(self):
        differ = MODULE.ActionDiffer()
        differ.feed(snapshot())
        self.assertEqual(differ.feed(snapshot(11))['record_kind'], 'interval')
        reset = differ.feed(snapshot(1, buildings=[construction()]))
        self.assertEqual(reset['reason'], 'game_time_rollback')
        self.assertNotIn('events', reset)
        self.assertEqual(differ.feed(snapshot(2, session_key='match-b'))['reason'], 'session_identity_changed')

    def test_native_log_prefix_reset_is_detected(self):
        differ = MODULE.ActionDiffer()
        old = [{'index': 0, 'time': 0, 'kind': 'SetWinter', 'params': [False]}]
        differ.feed(snapshot(native_events=old))
        self.assertEqual(differ.feed(snapshot(11, native_events=[]))['reason'], 'native_log_replaced_or_truncated')

    def test_rejected_sample_does_not_erase_baseline(self):
        differ = MODULE.ActionDiffer()
        differ.feed(snapshot())
        self.assertEqual(differ.feed({'status': 'error'})['record_kind'], 'sample_rejected')
        later = differ.feed(snapshot(15, buildings=[construction()]))
        self.assertEqual(later['time_interval']['start_game_seconds'], 10)
        self.assertEqual(later['skipped_samples_since_previous_valid'], 1)

    def test_review_preserves_segments_and_no_fake_completed_game(self):
        review = MODULE.make_action_review([snapshot(), snapshot(11), snapshot(2)])
        self.assertEqual(review['segment_count'], 2)
        self.assertIsNone(review['game_finished'])


if __name__ == '__main__':
    unittest.main()
