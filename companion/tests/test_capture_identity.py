"""Regression tests for destructive-looking but legal cross-match collisions."""
from copy import deepcopy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from capture_identity import CaptureIdentityTracker, history_join_decisions, runtime_session_key
from action_diff import ActionDiffer, diff_snapshots, make_action_review
from match_review import make_review


def event(at=11, kind='AddBuilding'):
    return {'index': 0, 'time': at, 'kind': kind, 'params': [
        {'entity_uid': 31}, {'zone_id': 82}, 'House', {'x': 100, 'y': 120}]}


def sample(at=10, **extra):
    result = {'status': 'ok', 'session_key': 'a', 'capture_run_id': 'run',
              'capture_segment_id': 'run:1', 'pid': 12, 'clan': 'Bear',
              'local_player_entity_uid': 31, 'game_time_seconds': at,
              'population_cap': 6, 'stocks': {'Food': 50}, 'units': [], 'buildings': []}
    result.update(extra)
    return result


def history(**extra):
    result = {'session_key': 'a', 'pid': 12, 'local_player_entity_uid': 31,
              'game_time_seconds': 20, 'logger_last_time': 20, 'game_finished': False,
              'game_build': 'verified', 'events': [event()], 'players': [
                  {'local_player': True, 'clan': 'Bear', 'curves': {
                      'food': [{'time': 0, 'value': 50}, {'time': 20, 'value': 75}]}}]}
    result.update(extra)
    return result


def construction():
    return {'uid': 9, 'kind': 'Construction', 'is_construction': True,
            'target_kind': 'House', 'zone_id': 82, 'position': {'x': 100, 'y': 120}}


def intent(**extra):
    result = {'session_key': 'a', 'capture_run_id': 'run', 'capture_segment_id': 'run:1',
              'kind': 'cancel_construction', 'status': 'confirmed',
              'game_time_seconds': 11, 'target': {'uid': 9}}
    result.update(extra)
    return result


class CaptureIdentityTests(unittest.TestCase):
    def test_independent_readers_derive_same_loaded_session(self):
        inputs = dict(pid=12, game_address=0x1000, state_address=0x2000,
                      local_player_address=0x3000, state_uid=5, local_player_uid=31,
                      load_time=123.5, game_build='verified')
        self.assertEqual(runtime_session_key(**inputs), runtime_session_key(**deepcopy(inputs)))
        original = runtime_session_key(**inputs)
        for field, value in [('pid', 13), ('state_address', 0x4000), ('load_time', 124.5),
                             ('local_player_uid', 32), ('game_build', 'updated')]:
            self.assertNotEqual(original, runtime_session_key(**dict(inputs, **{field: value})))

    def test_repeated_samples_keep_run_and_segment(self):
        tracker = CaptureIdentityTracker('run')
        first, later = tracker.observe('a', 10, []), tracker.observe('a', 11, [])
        self.assertEqual(first['session_key'], later['session_key'])
        self.assertEqual(first['capture_segment_id'], later['capture_segment_id'])
        self.assertIsNone(later['capture_segment_reason'])
        other_run = CaptureIdentityTracker('other').observe('a', 11, [])
        self.assertEqual(first['session_key'], other_run['session_key'])
        self.assertNotEqual(first['capture_segment_id'], other_run['capture_segment_id'])

    def test_rollback_and_log_replacement_create_segments(self):
        tracker = CaptureIdentityTracker('run')
        tracker.observe('a', 10, [event(5)])
        rollback = tracker.observe('a', 1, [])
        self.assertEqual(rollback['capture_segment_reason'], 'game_time_rollback')
        tracker.observe('a', 10, [event(5)])
        replacement = tracker.observe('a', 11, [event(5, 'Colonize')])
        self.assertEqual(replacement['capture_segment_reason'], 'native_log_replaced_or_truncated')
        self.assertNotEqual(rollback['capture_segment_id'], replacement['capture_segment_id'])

    def test_missing_snapshot_identity_is_rejected(self):
        row = sample()
        row.pop('session_key')
        self.assertEqual(ActionDiffer().feed(row)['status'], 'missing_session_identity')
        with self.assertRaises(ValueError):
            diff_snapshots(row, dict(row, game_time_seconds=11))

    def test_match_review_does_not_join_same_clan_and_time_other_game(self):
        rows = [sample(session_key='another'), sample(11, session_key='another', population_cap=11)]
        review = make_review(history(), rows)
        self.assertEqual(review['local_state_samples_used'], 0)
        self.assertEqual(review['observed_population_cap_changes'], [])
        self.assertEqual(review['identity_join']['decisions'][0]['reason'], 'session_identity_mismatch')

    def test_match_review_accepts_correct_session_single_segment(self):
        review = make_review(history(), [sample(), sample(11, population_cap=11)])
        self.assertEqual(review['local_state_samples_used'], 2)
        self.assertEqual(review['observed_population_cap_changes'][0]['after'], 11)

    def test_legacy_history_missing_identity_rejected_unless_audited(self):
        old = history()
        old.pop('session_key')
        self.assertEqual(make_review(old, [sample()])['local_state_samples_used'], 0)
        audit = {'session_key': 'a', 'reviewed_by': 'test operator',
                 'evidence': 'fixture records intentionally generated in one loaded match'}
        self.assertEqual(make_review(old, [sample()], audited_provenance=audit)['local_state_samples_used'], 1)
        self.assertEqual(make_review(history(session_key='wrong'), [sample()],
                                     audited_provenance=audit)['local_state_samples_used'], 0)

    def test_rollback_segments_are_not_sorted_and_silently_combined(self):
        rows = [sample(10), sample(12), sample(1), sample(11)]
        review = make_review(history(), rows)
        self.assertEqual(review['local_state_samples_used'], 0)
        audit = {'session_key': 'a', 'reviewed_by': 'test operator', 'evidence': 'selected post-load fixture',
                 'sample_segment_index': 2}
        chosen = make_review(history(), rows, audited_provenance=audit)
        self.assertEqual(chosen['local_state_samples_used'], 2)

    def test_log_replacement_and_multiple_capture_runs_are_ambiguous(self):
        rows = [sample(10, native_events=[event(5)]),
                sample(11, native_events=[event(5, 'Colonize')])]
        # A final native history can identify a single compatible log lineage.
        allowed, _ = history_join_decisions(history(events=[event(5, 'Colonize')]), rows)
        self.assertEqual(allowed, {1})
        allowed, info = history_join_decisions(history(), [sample(), sample(11, capture_run_id='other', capture_segment_id='other:1')])
        self.assertEqual(allowed, set())
        self.assertEqual(info['decisions'][0]['reason'], 'ambiguous_capture_segments')

    def test_external_history_cross_match_cannot_prove_completion(self):
        rows = [sample(buildings=[construction()]), sample(11)]
        review = make_action_review(rows, history(session_key='wrong'))
        self.assertEqual(review['event_counts'], {'construction_removed_unresolved': 1})
        self.assertEqual(review['external_history_join']['accepted_sample_count'], 0)

    def test_external_history_same_match_can_prove_completion(self):
        rows = [sample(buildings=[construction()]), sample(11)]
        review = make_action_review(rows, history())
        self.assertEqual(review['event_counts'], {'construction_completed': 1})
        self.assertEqual(review['external_history_join']['accepted_sample_count'], 2)

    def test_unidentified_bare_native_events_do_not_prove_completion(self):
        result = diff_snapshots(sample(buildings=[construction()]), sample(11), native_events=[event()])
        self.assertEqual(result['events'][0]['kind'], 'construction_removed_unresolved')
        differ = ActionDiffer()
        differ.feed(sample(buildings=[construction()]))
        result = differ.feed(sample(11), native_events=[event()])
        self.assertEqual(result['new_native_events'], [])
        self.assertEqual(result['rejected_external_native_event_count'], 1)

    def test_embedded_native_events_are_bound_to_snapshot_context(self):
        differ = ActionDiffer()
        differ.feed(sample(buildings=[construction()], native_events=[]))
        result = differ.feed(sample(11, native_events=[event()]))
        self.assertEqual(result['events'][0]['kind'], 'construction_completed')

    def test_intent_needs_correct_match_and_recording_segment(self):
        before, after = sample(buildings=[construction()]), sample(11)
        for bad in (intent(session_key='other'), intent(capture_segment_id='run:2'),
                    intent(capture_run_id='other'), dict(intent(), capture_segment_id=None)):
            result = diff_snapshots(before, after, intents=[bad])
            self.assertEqual(result['events'][0]['kind'], 'construction_removed_unresolved')
        self.assertEqual(diff_snapshots(before, after, intents=[intent()])['events'][0]['kind'],
                         'construction_cancelled')


if __name__ == '__main__':
    unittest.main()
