"""Known-zone optional failures preserve recording; no live game is used."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from collector import collect
import test_base_ring_collector as base_fixture


def details(status='partial'):
    return {'status': status, 'game_time_seconds': 0,
            'zones': [{'zone_id': 1}, {'zone_id': 2}],
            'reason_codes': ['known_zone_visibility_scope_limited'],
            'unknown_fields': ['complete_resource_and_neutral_coverage']}


class KnownZoneCollectorTests(unittest.TestCase):
    def run_loop(self, rows):
        return base_fixture.BaseRingCollectorTests().run_loop(rows)

    def test_current_component_reaches_stream_latest_health_and_manifest(self):
        row = base_fixture.observation(finished=True)
        row['known_zone_details'] = details()
        result = self.run_loop([row])
        self.assertEqual(result['player-observations.ndjson'][0]['known_zone_details'], details())
        self.assertEqual(result['latest.json']['known_zone_details'], details())
        health = result['health.ndjson'][0]
        self.assertEqual(health['status'], 'ok')
        self.assertEqual(health['known_zone_details_status'], 'partial')
        self.assertEqual(health['component_diagnostics']['known_zone_details']['source_status'], 'partial')
        self.assertEqual(health['component_diagnostics']['known_zone_details']['projection_status'], 'partial')
        self.assertEqual(health['known_zone_details_unknown_fields'],
                         ['complete_resource_and_neutral_coverage'])
        summary = result['manifest.json']['last_known_zone_details']
        self.assertEqual(summary['observed_zone_count'], 2)
        self.assertEqual(summary['coverage'], 'supported_subset_not_complete_known_map')
        self.assertEqual(result['manifest.json']['component_health_counts']['known_zone_details:partial'], 1)

    def test_optional_failure_preserves_own_observation_and_unknown_count(self):
        row = base_fixture.observation(finished=True)
        row['known_zone_details'] = {'status': 'unavailable',
            'reason_codes': ['known_zone_component_read_failed'], 'unknown_fields': ['all']}
        result = self.run_loop([row])
        own = result['player-observations.ndjson'][0]
        self.assertEqual(own['status'], 'ok')
        self.assertEqual(own['stocks'], row['stocks'])
        self.assertEqual(result['health.ndjson'][0]['known_zone_details_status'], 'unavailable')
        self.assertIsNone(result['manifest.json']['last_known_zone_details']['observed_zone_count'])

    def test_missing_after_read_error_does_not_reuse_previous_component(self):
        first = base_fixture.observation()
        first['known_zone_details'] = details()
        result = self.run_loop([first, OSError('synthetic private error'), base_fixture.observation(2, finished=True)])
        self.assertNotIn('known_zone_details', result['latest.json'])
        summary = result['manifest.json']['last_known_zone_details']
        self.assertEqual(summary['status'], 'missing')
        self.assertIsNone(summary['observed_zone_count'])
        last = result['health.ndjson'][-1]
        self.assertIsNone(last['known_zone_details_status'])
        self.assertEqual(last['component_diagnostics']['known_zone_details']['projection_status'], 'missing')

    def test_multiplayer_unsupported_does_not_become_empty_known_map(self):
        row = base_fixture.observation(finished=True)
        row['known_zone_details'] = {'status': 'unsupported_context', 'zones': [],
            'reason_codes': ['known_zone_multiplayer_unsupported']}
        result = self.run_loop([row])
        summary = result['manifest.json']['last_known_zone_details']
        self.assertEqual(summary['status'], 'unsupported_context')
        self.assertIsNone(summary['observed_zone_count'])
        self.assertEqual(result['health.ndjson'][0]['status'], 'ok')

    def test_diagnostics_excludes_private_text_from_optional_error(self):
        raw = {'known_zone_details': {'status': 'unavailable', 'error_type': 'LayoutError',
            'reason_codes': ['known_zone_component_read_failed', 'C:/private/user'],
            'error': '0x123456 C:/private/user', 'owner_name': 'private-name'}}
        projected = {'known_zone_details': {'status': 'rejected',
            'reason_codes': ['invalid_known_zone_component']}}
        result = collect.component_diagnostics(raw, projected)['known_zone_details']
        self.assertEqual((result['source_status'], result['projection_status']), ('unavailable', 'rejected'))
        self.assertEqual(result['source_reason_codes'], ['known_zone_component_read_failed'])
        self.assertNotIn('private', json.dumps(result))
        self.assertNotIn('0x123456', json.dumps(result))

    def test_final_read_error_clears_previous_map_summary(self):
        first = base_fixture.observation()
        first['known_zone_details'] = details()
        original = collect.argument_parser

        def duration_parser():
            parser = original()
            parse_args = parser.parse_args
            parser.parse_args = lambda args: parse_args([*args, '--seconds', '0.25'])
            return parser

        with patch.object(collect, 'argument_parser', side_effect=duration_parser):
            result = self.run_loop([first, *[OSError('synthetic read error') for _ in range(10)]])
        self.assertEqual(result['health.ndjson'][-1]['status'], 'collector_read_error')
        self.assertEqual(result['latest.json']['status'], 'collector_read_error')
        summary = result['manifest.json']['last_known_zone_details']
        self.assertEqual(summary['status'], 'unavailable')
        self.assertIsNone(summary['observed_zone_count'])
        self.assertIsNone(summary['game_time_seconds'])
        self.assertEqual(result['manifest.json']['component_health_counts']['known_zone_details:partial'], 1)


if __name__ == '__main__':
    unittest.main()
