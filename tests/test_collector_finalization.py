"""Offline finalization preserves the stopped boundary and raw capture bytes."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collector import collect


class CollectorFinalizationTests(unittest.TestCase):
    def finalize(self, output, manifest, publications=None):
        collect.finalize_manifest(output, manifest, publications or collect.LocalPublications(),
                                  stop_reason='stop_file', counts={'ok': 2}, sequence=2)

    def test_stopped_pending_is_on_disk_before_reconciliation_starts(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            manifest = {'recording_state': 'recording', 'economic_reconciliation': {'stale': True}}

            def reconcile(path):
                saved = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
                self.assertEqual(path, output / 'economic-snapshots.ndjson')
                self.assertEqual(saved['recording_state'], 'stopped')
                self.assertEqual(saved['stop_reason'], 'stop_file')
                self.assertEqual(saved['records_attempted'], 2)
                self.assertEqual(saved['postprocessing']['economic_reconciliation'], {'status': 'pending'})
                self.assertNotIn('economic_reconciliation', saved)
                return {'intervals': 1, 'statuses': {'accepted': 1}, 'resources': {}}

            with patch.object(collect, 'reconcile_file', side_effect=reconcile) as operation:
                self.finalize(output, manifest)
            operation.assert_called_once()

    def test_parse_failure_keeps_stopped_and_does_not_modify_damaged_capture(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            source = output / 'economic-snapshots.ndjson'
            raw = b'{"status":"collector_read_error"}\n{"status":'
            source.write_bytes(raw)
            self.finalize(output, {'recording_state': 'recording'})
            saved = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['recording_state'], 'stopped')
            self.assertEqual(saved['stop_reason'], 'stop_file')
            self.assertEqual(saved['postprocessing']['economic_reconciliation'], {
                'status': 'failed', 'reason_code': 'capture_json_parse_error',
                'error_type': 'JSONDecodeError'})
            self.assertNotIn('economic_reconciliation', saved)
            self.assertEqual(source.read_bytes(), raw)

    def test_normal_summary_structure_and_stop_reason_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            summary = {'schema_version': 1, 'intervals': 3, 'statuses': {'not_reconcilable': 3},
                       'resources': {'Food': {'accepted_native_cycle_seconds': 0}}, 'notice': 'synthetic'}
            original = deepcopy(summary)
            with patch.object(collect, 'reconcile_file', return_value=summary):
                self.finalize(output, {'recording_state': 'recording'})
            saved = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['economic_reconciliation'], original)
            self.assertEqual(saved['stop_reason'], 'stop_file')
            self.assertEqual(saved['status_counts'], {'ok': 2})
            self.assertEqual(saved['postprocessing']['economic_reconciliation'], {'status': 'completed'})
            self.assertEqual(summary, original)

    def test_known_io_and_processing_failures_record_safe_diagnostics(self):
        for error, status, reason in (
                (OSError('sensitive-local-path'), 'unavailable', 'capture_io_error'),
                (TypeError('private-source-fragment'), 'failed', 'economic_reconciliation_processing_error')):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as folder:
                output = Path(folder)
                with patch.object(collect, 'reconcile_file', side_effect=error):
                    self.finalize(output, {'recording_state': 'recording'})
                saved = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
                self.assertEqual(saved['recording_state'], 'stopped')
                self.assertEqual(saved['postprocessing']['economic_reconciliation'], {
                    'status': status, 'reason_code': reason, 'error_type': type(error).__name__})
                self.assertNotIn(str(error), json.dumps(saved))
                self.assertNotIn('economic_reconciliation', saved)

    def test_final_publication_failure_propagates_and_leaves_stopped_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            publish = collect.atomic_json
            calls = []

            def atomic(path, value):
                calls.append(deepcopy(value))
                return publish(path, value) if len(calls) == 1 else False

            with patch.object(collect, 'atomic_json', side_effect=atomic), \
                    patch.object(collect, 'reconcile_file', return_value={'intervals': 1}):
                with self.assertRaisesRegex(OSError, 'Required local capture metadata publication failed'):
                    self.finalize(output, {'recording_state': 'recording'})
            saved = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(len(calls), 2)
            self.assertEqual(saved['recording_state'], 'stopped')
            self.assertEqual(saved['postprocessing']['economic_reconciliation'], {'status': 'pending'})
            self.assertNotIn('economic_reconciliation', saved)

    def test_pending_publication_failure_stops_before_postprocessing(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(collect, 'atomic_json', return_value=False), \
                    patch.object(collect, 'reconcile_file') as reconcile:
                with self.assertRaisesRegex(OSError, 'Required local capture metadata publication failed'):
                    self.finalize(Path(folder), {'recording_state': 'recording'})
            reconcile.assert_not_called()


if __name__ == '__main__':
    unittest.main()
