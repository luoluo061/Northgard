"""An operator journal cannot invent a recording run or reuse a stale branch."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('record_choice', ROOT / 'research/telemetry/record_choice.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
from capture_identity import native_log_checkpoint

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def snapshot(**extra):
    row = {'status': 'ok', 'session_key': 'a', 'capture_run_id': 'recording',
           'capture_segment_id': 'recording:2', 'capture_segment_index': 2,
           'pid': 12, 'local_player_entity_uid': 31, 'clan': 'Bear',
           'captured_at': NOW.isoformat(), 'game_time_seconds': 100,
           'native_events': [{'index': 0, 'time': 1, 'kind': 'SetWinter', 'params': [False]}]}
    row.update(extra)
    return row


class ChoiceIdentityTests(unittest.TestCase):
    def bind(self, latest=None, live=None):
        return MODULE.bind_capture_identity(latest or snapshot(), live or snapshot(), now=NOW)

    def test_fresh_independent_reader_reuses_recording_identity(self):
        live = snapshot(capture_run_id='validation-probe', capture_segment_id='validation-probe:1',
                        game_time_seconds=101)
        bound = self.bind(live=live)
        self.assertEqual(bound['capture_run_id'], 'recording')
        self.assertEqual(bound['capture_segment_id'], 'recording:2')
        self.assertEqual(bound['capture_validation_game_gap_seconds'], 1)

    def test_stale_future_and_timezone_free_timestamps_rejected(self):
        for value in ((NOW - timedelta(seconds=11)).isoformat(),
                      (NOW + timedelta(seconds=2)).isoformat(), NOW.replace(tzinfo=None).isoformat()):
            with self.assertRaises(ValueError):
                self.bind(latest=snapshot(captured_at=value))
        with self.assertRaises(ValueError):
            self.bind(live=snapshot(captured_at=(NOW - timedelta(seconds=11)).isoformat()))

    def test_other_loaded_game_or_player_rejected(self):
        for extra in ({'session_key': 'other'}, {'pid': 13}, {'local_player_entity_uid': 32}, {'clan': 'Raven'}):
            with self.assertRaises(ValueError):
                self.bind(live=snapshot(**extra))

    def test_rollback_and_large_game_gap_rejected(self):
        for at in (99.9, 106, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.bind(live=snapshot(game_time_seconds=at))

    def test_native_log_replacement_or_truncation_rejected(self):
        for events in ([], [{'index': 0, 'time': 1, 'kind': 'SetWinter', 'params': [True]}]):
            with self.assertRaises(ValueError):
                self.bind(live=snapshot(native_events=events))

    def test_checkpoint_allows_content_free_latest_but_not_different_branch(self):
        latest = snapshot()
        latest['native_log_checkpoint'] = native_log_checkpoint(latest.pop('native_events'))
        self.assertEqual(self.bind(latest=latest)['capture_segment_id'], 'recording:2')
        for checkpoint in ({'event_count': 1, 'sha256': 'bad'}, {'event_count': 2, 'sha256': 'bad'},
                           {'event_count': True, 'sha256': 'bad'}):
            bad = dict(latest, native_log_checkpoint=checkpoint)
            with self.assertRaises(ValueError):
                self.bind(latest=bad)

    def test_missing_capture_identity_or_lineage_is_not_guessed(self):
        for field in ('session_key', 'capture_run_id', 'capture_segment_id', 'native_events'):
            latest = snapshot()
            latest.pop(field)
            with self.assertRaises(ValueError):
                self.bind(latest=latest)
        with self.assertRaises(ValueError):
            self.bind(latest=snapshot(status='error'))

    def test_validation_does_not_modify_recording_or_live_snapshot(self):
        latest, live = snapshot(), snapshot()
        originals = deepcopy((latest, live))
        self.bind(latest, live)
        self.assertEqual((latest, live), originals)


if __name__ == '__main__':
    unittest.main()
