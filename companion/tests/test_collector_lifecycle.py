"""Collector lifecycle regressions. Uses fake readers only; never opens a game."""
from __future__ import annotations

import contextlib
import io
import itertools
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from collector import collect


class FakeReader:
    def __init__(self, pid):
        self.pid = pid
        self.closed = False

    def close(self):
        self.closed = True


class ConnectionTests(unittest.TestCase):
    def factories(self, pids):
        readers, probes = [], []

        def reader_factory(pid):
            reader = FakeReader(pid)
            readers.append(reader)
            return reader

        def probe_factory(reader):
            probe = SimpleNamespace(compatibility={'data_cdb_verified': True},
                                    profile={'game_build': 'test'},
                                    sample=Mock(return_value={'capture_run_id': f'run-{len(probes)}'}))
            probes.append(probe)
            return probe

        connection = collect.ProbeConnection(pid_finder=Mock(side_effect=pids),
                                             reader_factory=reader_factory,
                                             probe_factory=probe_factory)
        return connection, readers, probes

    def test_unchanged_pid_reuses_verified_connection(self):
        connection, readers, probes = self.factories([10, 10])
        self.assertEqual(connection.sample(), connection.sample())
        self.assertEqual(len(readers), 1)
        self.assertEqual(len(probes), 1)
        self.assertEqual(connection.generation, 1)
        connection.close()
        self.assertTrue(readers[0].closed)
        self.assertIsNone(connection.probe)

    def test_pid_change_without_observed_exit_closes_and_recreates(self):
        connection, readers, probes = self.factories([10, 20])
        first = connection.sample()
        second = connection.sample()
        self.assertTrue(readers[0].closed)
        self.assertFalse(readers[1].closed)
        self.assertNotEqual(first['capture_run_id'], second['capture_run_id'])
        self.assertEqual(connection.generation, 2)

    def test_disappeared_process_drops_stale_probe_and_later_reconnects(self):
        connection, readers, probes = self.factories([10, ValueError('Expected one Northgard.exe, found 0'), 20])
        connection.sample()
        with self.assertRaises(ValueError):
            connection.sample()
        self.assertTrue(readers[0].closed)
        self.assertIsNone(connection.reader)
        self.assertIsNone(connection.probe)
        self.assertEqual(connection.sample()['capture_run_id'], 'run-1')

    def test_failed_old_handle_is_dropped_even_if_pid_is_reused(self):
        connection, readers, probes = self.factories([10, 10, 10])
        connection.sample()
        probes[0].sample.side_effect = OSError('old process handle exited')
        with self.assertRaises(OSError):
            connection.sample()
        self.assertTrue(readers[0].closed)
        self.assertEqual(connection.sample()['capture_run_id'], 'run-1')
        self.assertEqual(connection.generation, 2)

    def test_probe_constructor_failure_always_closes_opened_handle(self):
        reader = FakeReader(10)
        connection = collect.ProbeConnection(pid_finder=lambda: 10,
                                             reader_factory=lambda pid: reader,
                                             probe_factory=Mock(side_effect=ValueError('unsupported build')))
        with self.assertRaises(ValueError):
            connection.sample()
        self.assertTrue(reader.closed)
        self.assertIsNone(connection.probe)

    def test_unverified_rules_are_rejected_and_handle_is_closed(self):
        reader = FakeReader(10)
        connection = collect.ProbeConnection(pid_finder=lambda: 10,
                                             reader_factory=lambda pid: reader,
                                             probe_factory=lambda reader: SimpleNamespace(compatibility={'data_cdb_verified': False}))
        with self.assertRaisesRegex(ValueError, 'verified data.cdb'):
            connection.sample()
        self.assertTrue(reader.closed)
        self.assertEqual(connection.generation, 0)


class CollectorLoopTests(unittest.TestCase):
    @staticmethod
    def row(time, finished=False):
        return {'status': 'ok', 'game_time_seconds': time, 'session_game_finished': finished,
                'capture_run_id': 'fake-run', 'capture_segment_id': 'fake-segment',
                'session_key': 'fake-session', 'clan': 'Bear'}

    def run_loop(self, rows, *, args=(), clock=None, stop_file=False):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'capture'
            if stop_file:
                output.mkdir()
                (output / 'STOP').touch()
            probe = SimpleNamespace(compatibility={'data_cdb_verified': True}, profile={'game_build': 'test'})
            connection = SimpleNamespace(generation=0, probe=probe, close=Mock())
            iterator = iter(rows)

            def sample():
                connection.generation = 1
                result = next(iterator)
                if isinstance(result, BaseException):
                    raise result
                return result

            connection.sample = Mock(side_effect=sample)
            differ = SimpleNamespace(feed=Mock(return_value={}))
            with patch.object(collect, 'ProbeConnection', return_value=connection), \
                 patch.object(collect, 'local_setup_ready', return_value=True), \
                 patch.object(collect, 'ActionDiffer', return_value=differ), \
                 patch.object(collect, 'project_player_observation', side_effect=lambda row, **kwargs: dict(row)), \
                 patch.object(collect, 'reconcile_file', return_value={}), \
                 patch.object(collect.time, 'monotonic', side_effect=clock or itertools.count(0, 0.1)), \
                 patch.object(collect.time, 'sleep'), \
                 contextlib.redirect_stdout(io.StringIO()):
                collect.main(['--out-dir', str(output), *args])
            manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
            self.assertTrue(connection.close.called)
            return manifest, connection

    def test_default_has_no_duration_limit_and_runs_beyond_one_hour(self):
        manifest, connection = self.run_loop([self.row(1), self.row(2, True)], clock=itertools.count(0, 2000))
        self.assertIsNone(manifest['duration_wall_seconds_limit'])
        self.assertEqual(manifest['stop_reason'], 'game_finished')
        self.assertEqual(manifest['records_attempted'], 2)
        self.assertEqual(connection.sample.call_count, 2)

    def test_zero_seconds_still_attempts_exactly_one_snapshot(self):
        manifest, connection = self.run_loop([self.row(1)], args=['--seconds', '0'])
        self.assertEqual(manifest['stop_reason'], 'single_snapshot')
        self.assertEqual(manifest['records_attempted'], 1)
        self.assertEqual(connection.sample.call_count, 1)

    def test_explicit_positive_limit_is_a_test_duration(self):
        manifest, connection = self.run_loop([self.row(1)], args=['--seconds', '1'], clock=iter([0, 0, 2]))
        self.assertEqual(manifest['stop_reason'], 'duration_limit')
        self.assertEqual(manifest['duration_wall_seconds_limit'], 1)
        self.assertEqual(connection.sample.call_count, 1)

    def test_stop_file_stops_unlimited_recording(self):
        manifest, connection = self.run_loop([], stop_file=True)
        self.assertEqual(manifest['stop_reason'], 'stop_file')
        self.assertEqual(manifest['records_attempted'], 0)
        connection.sample.assert_not_called()

    def test_keyboard_interrupt_still_finalizes_manifest(self):
        manifest, connection = self.run_loop([KeyboardInterrupt()])
        self.assertEqual(manifest['stop_reason'], 'keyboard_interrupt')
        self.assertEqual(manifest['recording_state'], 'stopped')


if __name__ == '__main__':
    unittest.main()
