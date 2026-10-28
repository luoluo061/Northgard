"""Offline capture-summary fixtures; no collector, game, or network access."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from summarize_capture import (MAX_LINE_BYTES, SummaryError, main, summarize_capture,
                               write_summary)


def observation(when=0, *, session='session-A', run='run-A', segment='segment-A',
                kind='Villager', events=(), **extra):
    value = {'status': 'ok', 'session_key': session, 'capture_run_id': run,
             'capture_segment_id': segment, 'game_time_seconds': when,
             'captured_at': '2026-10-28T12:00:00+08:00',
             'stocks': {name: 100 for name in ('Food', 'Wood', 'Money', 'Stone', 'Iron', 'Lore')},
             'population': 1, 'population_cap': 6,
             'units': [{'uid': 1, 'kind': kind, 'assigned_job': {'kind': 'Work'},
                        'cur_job': {'kind': 'Active'}}],
             'own_events': list(events), 'economy': {'status': 'ok'},
             'labor': {'status': 'partial'}}
    value.update(extra)
    return value


def native_event(index=0, when=0, kind='Colonize'):
    return {'index': index, 'time': when, 'kind': kind, 'actor': 'self',
            'source': 'native_own_success_event', 'zone_id': 9}


class CaptureSummaryTests(unittest.TestCase):
    def capture(self, folder, rows=(), *, state='stopped', health=()):
        capture = Path(folder) / 'capture'
        capture.mkdir()
        (capture / 'manifest.json').write_text(json.dumps({
            'recording_state': state, 'stop_reason': 'stop_file', 'collector_version': 'synthetic'}), encoding='utf-8')
        for filename, values in (('player-observations.ndjson', rows), ('health.ndjson', health)):
            with (capture / filename).open('w', encoding='utf-8', newline='\n') as handle:
                for value in values:
                    handle.write(json.dumps(value, allow_nan=False) + '\n')
        return capture

    def test_multiple_sessions_reusing_clock_never_mix_stocks_or_events(self):
        early = observation(20, events=[native_event(0, 10)])
        later = observation(20, session='session-B', events=[native_event(0, 12, 'AddBuilding')])
        later['stocks']['Food'] = 999
        with tempfile.TemporaryDirectory() as folder:
            summary = summarize_capture(self.capture(folder, [early, later]))
        first, second = summary['groups']
        self.assertEqual((first['session_key'], second['session_key']), ('session-A', 'session-B'))
        self.assertEqual(first['last_state']['stocks']['Food'], 100)
        self.assertEqual(second['first_state']['stocks']['Food'], 999)
        self.assertEqual(first['native_events']['events'][0]['event']['kind'], 'Colonize')
        self.assertEqual(second['native_events']['events'][0]['event']['kind'], 'AddBuilding')
        self.assertEqual(first['native_events']['index_conflicts'], [])

    def test_equal_game_time_job_changes_keep_bucket_end_and_first_state(self):
        rows = [observation(10, kind='Villager'), observation(10, kind='Woodcutter')]
        rows[1]['population_cap'] = 10
        with tempfile.TemporaryDirectory() as folder:
            group = summarize_capture(self.capture(folder, rows))['groups'][0]
        self.assertEqual(group['observation_rows'], 2)
        self.assertEqual(group['clock']['unique_game_times'], 1)
        self.assertEqual(group['clock']['stagnant_clock_observations'], 1)
        bucket = group['calendar_buckets'][0]
        self.assertEqual(bucket['first_state']['unit_kind_counts'], {'Villager': 1})
        self.assertEqual(bucket['last_state']['unit_kind_counts'], {'Woodcutter': 1})
        self.assertEqual(bucket['last_state']['population_cap'], 10)

    def test_same_session_new_run_or_segment_never_creates_cross_boundary_interval(self):
        rows = [observation(10), observation(100, run='run-B'),
                observation(200, run='run-B', segment='segment-B')]
        with tempfile.TemporaryDirectory() as folder:
            groups = summarize_capture(self.capture(folder, rows))['groups']
        self.assertEqual(len(groups), 3)
        for group in groups:
            self.assertIsNone(group['clock']['maximum_positive_interval_game_seconds'])
            self.assertEqual(group['clock']['suspicious_sampling_interval_count'], 0)
            self.assertNotIn('production', group)

    def test_bad_rows_missing_fields_nonfinite_and_incomplete_tail_are_diagnosed(self):
        row = observation(10)
        missing_identity = observation(11)
        del missing_identity['capture_segment_id']
        del row['population']
        row['stocks']['Wood'] = None
        row['stocks']['Stone'] = 10**400  # Huge finite integers are invalid fields, not crashes.
        with tempfile.TemporaryDirectory() as folder:
            capture = self.capture(folder, [row, missing_identity])
            with (capture / 'player-observations.ndjson').open('ab') as handle:
                handle.write(b'{bad}\n{"game_time_seconds": NaN}\n{"game_time_seconds": 1e999}\n')
                handle.write(b'{"status":')
            summary = summarize_capture(capture)
        self.assertEqual(summary['ungrouped_observation_rows'], 1)
        counts = summary['diagnostics']['counts']
        self.assertEqual(counts['invalid_json'], 2)
        self.assertEqual(counts['non_finite_json'], 2)
        self.assertEqual(counts['incomplete_tail'], 1)
        self.assertEqual(counts['missing_or_invalid_identity'], 1)
        self.assertIsNone(summary['groups'][0]['first_state']['population'])
        self.assertIsNone(summary['groups'][0]['first_state']['stocks']['Wood'])
        self.assertIn('missing_or_null_field:population', summary['diagnostics']['field_counts'])
        json.dumps(summary, allow_nan=False)

    def test_stagnant_clock_adds_no_time_or_suspicious_gap(self):
        with tempfile.TemporaryDirectory() as folder:
            clock = summarize_capture(self.capture(folder, [observation(0)] * 4))['groups'][0]['clock']
        self.assertEqual(clock['stagnant_clock_observations'], 3)
        self.assertEqual(clock['unique_game_times'], 1)
        self.assertEqual(clock['suspicious_sampling_interval_count'], 0)
        self.assertIsNone(clock['maximum_positive_interval_game_seconds'])
        self.assertNotIn('duration_seconds', clock)

    def test_native_events_deduplicate_content_and_keep_conflicting_index_variants(self):
        original, conflict = native_event(2, 5), native_event(2, 5, 'AddBuilding')
        unrelated = {**native_event(3, 5), 'actor': 'other'}
        rows = [observation(10, events=[original]),
                observation(11, events=[deepcopy(original), conflict, unrelated])]
        with tempfile.TemporaryDirectory() as folder:
            summary = summarize_capture(self.capture(folder, rows))
        events = summary['groups'][0]['native_events']
        self.assertEqual(events['observations'], 3)
        self.assertEqual(events['repeated_observations_deduplicated'], 1)
        self.assertEqual(events['unique_index_count'], 1)
        self.assertEqual(events['unique_content_count'], 2)
        self.assertEqual(events['index_conflicts'][0]['variant_count'], 2)
        self.assertEqual(events['events'][0]['observations'], 2)
        self.assertIn('native_event_index_content_conflict', summary['diagnostics']['counts'])
        self.assertIn('invalid_or_unverified_own_event', summary['diagnostics']['counts'])

    def test_stopped_manifest_is_required_and_output_creation_is_exclusive(self):
        with tempfile.TemporaryDirectory() as folder:
            capture = self.capture(folder, [observation()], state='recording')
            with self.assertRaisesRegex(SummaryError, 'recording_state=stopped'):
                summarize_capture(capture)
            (capture / 'manifest.json').write_text('{"recording_state":"stopped"}', encoding='utf-8')
            summary = summarize_capture(capture)
            output = Path(folder) / 'summary.json'
            write_summary(summary, output)
            original = output.read_bytes()
            with self.assertRaises(FileExistsError):
                write_summary({'replace': True}, output)
            self.assertEqual(output.read_bytes(), original)
            with contextlib.redirect_stdout(io.StringIO()) as console:
                self.assertEqual(main(['--capture', str(capture)]), 0)
            index = json.loads(console.getvalue())
            self.assertEqual(len(index['groups']), 1)
            self.assertNotIn('calendar_buckets', index['groups'][0])
            self.assertEqual(set(path.name for path in capture.iterdir()),
                             {'manifest.json', 'player-observations.ndjson', 'health.ndjson'})

    def test_oversized_physical_line_is_drained_and_next_record_remains_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            capture = self.capture(folder)
            with (capture / 'player-observations.ndjson').open('ab') as handle:
                handle.write(b'x' * (MAX_LINE_BYTES + 100) + b'\n')
                handle.write(json.dumps(observation(2)).encode() + b'\n')
                handle.write(b'[]\n')
            summary = summarize_capture(capture)
        self.assertEqual(summary['diagnostics']['counts']['line_too_large'], 1)
        self.assertEqual(summary['diagnostics']['counts']['non_object_row'], 1)
        self.assertEqual(summary['source_files']['player-observations.ndjson']['physical_lines'], 3)
        self.assertEqual(summary['groups'][0]['first_state']['source_line'], 2)

    def test_calendar_month_thirds_use_A800_march_and_missing_stream_is_explicit(self):
        rows = [observation(when) for when in (0, 19.999, 20, 40, 60, 600)]
        with tempfile.TemporaryDirectory() as folder:
            capture = self.capture(folder, rows)
            (capture / 'health.ndjson').unlink()
            summary = summarize_capture(capture)
        buckets = summary['groups'][0]['calendar_buckets']
        self.assertEqual([bucket['label'] for bucket in buckets],
                         ['A800-03-初', 'A800-03-中', 'A800-03-末', 'A800-04-初', 'A801-01-初'])
        self.assertEqual(buckets[0]['observation_rows'], 2)
        self.assertIn('missing_file', summary['diagnostics']['counts'])
        self.assertFalse(summary['source_files']['health.ndjson']['present'])

    def test_rejected_rows_and_identity_reentry_break_adjacent_interval_baselines(self):
        with tempfile.TemporaryDirectory() as folder:
            capture = self.capture(folder)
            records = [observation(10), b'{bad}', observation(11),
                       observation(12, session='session-B'), observation(12),
                       observation(13, status='rejected'), observation(14)]
            with (capture / 'player-observations.ndjson').open('ab') as handle:
                for record in records:
                    handle.write((record if isinstance(record, bytes) else json.dumps(record).encode()) + b'\n')
            summary = summarize_capture(capture)
        group = summary['groups'][0]
        self.assertIsNone(group['clock']['maximum_positive_interval_game_seconds'])
        self.assertEqual(group['source_contiguous_blocks'], 4)
        self.assertEqual(group['source_interruption_count'], 3)
        self.assertEqual(summary['diagnostics']['counts']['observation_stream_interrupted'], 4)
        self.assertEqual(summary['diagnostics']['counts']['observation_group_reentered'], 3)
        self.assertEqual(group['usable_state_rows'], 4)

    def test_manual_stop_is_not_native_end_state_and_missing_is_not_false(self):
        rows = [observation(0, session_game_finished=False, local_player_game_over=False),
                observation(1, session_game_finished=False, local_player_has_conceded=None)]
        with tempfile.TemporaryDirectory() as folder:
            capture = self.capture(folder, rows)
            summary = summarize_capture(capture)
            self.assertEqual(summary['stop_reason'], 'stop_file')
            group = summary['groups'][0]
            self.assertEqual(group['end_state_observation_counts']['session_game_finished'],
                             {'true': 0, 'false': 2, 'missing_or_invalid': 0})
            self.assertEqual(group['end_state_observation_counts']['local_player_game_over'],
                             {'true': 0, 'false': 1, 'missing_or_invalid': 1})
            self.assertEqual(group['end_state_observation_counts']['local_player_has_conceded'],
                             {'true': 0, 'false': 0, 'missing_or_invalid': 2})
            self.assertIsNone(group['last_state']['local_player_game_over'])
            (capture / 'manifest.json').write_text('{"recording_state":"stopped"}', encoding='utf-8')
            unknown = summarize_capture(capture)
            self.assertIsNone(unknown['stop_reason'])
            self.assertIn('missing_or_null_field:stop_reason', unknown['diagnostics']['field_counts'])

    def test_threshold_rollback_and_health_reasons_remain_separate(self):
        rows = [observation(0), observation(1), observation(5), observation(4)]
        health = [
            {'status': 'not_in_match', 'captured_at': '2026-10-28T11:59:00+08:00'},
            {'status': 'collector_read_error', 'reason_code': 'game_not_running_or_multiple',
             'captured_at': '2026-10-28T11:59:01+08:00'},
            {'status': 'ok', 'captured_at': '2026-10-28T12:00:00+08:00',
             'economy_status': 'not_ready'},
        ]
        with tempfile.TemporaryDirectory() as folder:
            capture = self.capture(folder, rows, health=health)
            summary = summarize_capture(capture, gap_threshold=2)
            with self.assertRaises(SummaryError):
                summarize_capture(capture, gap_threshold=float('nan'))
        clock = summary['groups'][0]['clock']
        self.assertEqual(clock['maximum_positive_interval_game_seconds'], 4)
        self.assertEqual(clock['suspicious_sampling_interval_count'], 1)
        self.assertEqual(clock['rollback_count'], 1)
        self.assertEqual(clock['first_game_time_seconds'], 0)
        self.assertEqual(clock['last_game_time_seconds'], 4)
        self.assertEqual(summary['health']['rows'], 3)
        self.assertEqual(summary['health']['reason_code_counts']['game_not_running_or_multiple'], 1)
        self.assertEqual(summary['health']['first_captured_at'], '2026-10-28T11:59:00+08:00')
        self.assertEqual(summary['health']['last_captured_at'], '2026-10-28T12:00:00+08:00')
        self.assertEqual(summary['health']['rows_without_complete_identity'], 3)
        self.assertEqual(summary['groups'][0]['component_status_counts']['labor'], {'partial': 4})


if __name__ == '__main__':
    unittest.main()
