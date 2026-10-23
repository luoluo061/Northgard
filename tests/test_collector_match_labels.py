"""Collector labeling regressions; fake readers only, no native process access."""
import contextlib
import io
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collector import collect
from companion.tests import test_collector_lifecycle as lifecycle

SHARED = '12345678-1234-4234-8234-123456789abc'


class CollectorMatchLabelTests(unittest.TestCase):
    def test_labels_are_declarations_not_native_team_ids(self):
        parser = collect.argument_parser()
        args = parser.parse_args(['--shared-match-id', SHARED, '--team-label', 'A',
                                  '--player-slot', '2', '--declared-format', '3v3'])
        self.assertEqual(collect.contributor_context(args, parser), {
            'shared_match_id': SHARED, 'team_label': 'A', 'player_slot': 2, 'declared_format': '3v3'})
        self.assertIsNone(collect.contributor_context(parser.parse_args([]), parser))
        self.assertEqual(collect.contributor_context(parser.parse_args(['--shared-match-id', SHARED]), parser), {
            'shared_match_id': SHARED, 'team_label': 'unknown', 'player_slot': None, 'declared_format': 'unknown'})

    def test_invalid_or_incomplete_labels_fail_before_creating_a_capture(self):
        for flags in (['--team-label', 'A'], ['--declared-format', '3v3'],
                      ['--shared-match-id', SHARED, '--player-slot', '1'],
                      ['--shared-match-id', 'not-a-uuid'],
                      ['--shared-match-id', SHARED, '--team-label', 'A', '--player-slot', '4']):
            with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser = collect.argument_parser()
                collect.contributor_context(parser.parse_args(flags), parser)

    def test_actual_loop_keeps_shared_labels_and_native_mode_separate(self):
        row = lifecycle.CollectorLoopTests.row(100, finished=True)
        row.update(game_context={'mode_kind': 'Multiplayer', 'mode_variant_kind': 'Team3V3'},
                   match_context={'status': 'ok', 'configured_team_layout': '3v3', 'native_local_team_id': 1})
        manifest, _ = lifecycle.CollectorLoopTests().run_loop([row], args=(
            '--shared-match-id', SHARED, '--team-label', 'A', '--player-slot', '2', '--declared-format', '3v3'))
        self.assertEqual(manifest['contributor_context']['team_label'], 'A')
        self.assertEqual(manifest['last_match_context']['native_local_team_id'], 1)
        self.assertEqual(manifest['last_mode_diagnostics']['capture_validation_stage'],
                         'experimental_multiplayer_unverified')
        self.assertEqual(manifest['release_live_multiplayer_validation'], 'not_run')

    def test_native_context_cannot_be_overridden_by_labels(self):
        link = {'declared_format': '3v3'}
        result = collect.mode_diagnostics({'game_context': {'mode_kind': 'Singleplayer'}}, link)
        self.assertEqual(result['contributor_link_status'], 'conflicts_with_native_context')
        result = collect.mode_diagnostics({'game_context': {'mode_kind': 'Multiplayer'},
            'match_context': {'configured_team_layout': '2v2'}}, link)
        self.assertEqual(result['contributor_link_status'], 'conflicts_with_native_context')
        result = collect.mode_diagnostics({}, link)
        self.assertEqual(result['capture_validation_stage'], 'experimental_multiplayer_unverified')
        self.assertIsNone(result['native_mode_kind'])
        self.assertEqual(result['contributor_link_status'], 'contributor_declared_native_layout_unverified')
        self.assertEqual(collect.mode_diagnostics({})['capture_validation_stage'], 'native_mode_unverified')
        variant_only = collect.mode_diagnostics({'game_context': {'mode_variant_kind': 'Team3V3'}})
        self.assertIsNone(variant_only['native_mode_kind'])
        self.assertEqual(variant_only['capture_validation_stage'], 'experimental_multiplayer_unverified')


if __name__ == '__main__':
    unittest.main()
