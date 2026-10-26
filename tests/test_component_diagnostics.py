"""Health diagnostics carry only fixed-schema codes, never exception text."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from collector.collect import component_diagnostics
from submission_contract import RECORD_SCHEMAS, SubmissionError, project, validate


class ComponentDiagnosticTests(unittest.TestCase):
    def test_source_and_projection_failures_remain_distinct_and_private_text_is_excluded(self):
        raw = {'population_growth': {'status': 'unavailable', 'error_type': 'LayoutError',
            'error': 'C:/private/person/0x123456', 'private_account': 'do-not-copy',
            'reason_codes': ['population_growth_component_read_failed', 'C:/do-not-copy'],
            'read_guard_issues': ['town_hall_player_cache_changed_during_read']}}
        projected = {'population_growth': {'status': 'rejected', 'reason_codes': ['invalid_or_racing_growth_component']}}
        value = component_diagnostics(raw, projected)
        row = value['population_growth']
        self.assertEqual((row['source_status'], row['projection_status']), ('unavailable', 'rejected'))
        self.assertEqual(row['source_reason_codes'], ['population_growth_component_read_failed'])
        self.assertEqual(row['error_type'], 'LayoutError')
        self.assertNotIn('private', json.dumps(value))
        self.assertNotIn('do-not-copy', json.dumps(value))
        self.assertEqual(value['economy']['source_status'], 'missing')

    def test_health_contract_accepts_only_the_fixed_diagnostic_tree(self):
        row = {'status': 'ok', 'component_diagnostics': component_diagnostics({}, {})}
        schema = RECORD_SCHEMAS['health.ndjson.gz']
        validate(row, schema)
        row['component_diagnostics']['economy']['raw_exception'] = 'do-not-copy'
        with self.assertRaises(SubmissionError):
            validate(row, schema)
        dropped = [0]
        safe = project(row, schema, dropped)
        validate(safe, schema)
        self.assertNotIn('raw_exception', safe['component_diagnostics']['economy'])
        self.assertEqual(dropped[0], 1)

    def test_malformed_component_shapes_never_break_health_logging(self):
        value = component_diagnostics({'economy': {'reason_codes': None, 'error_type': 'bad/path'}},
                                      {'economy': None})
        self.assertEqual(value['economy']['projection_status'], 'missing')
        self.assertEqual(value['economy']['source_reason_codes'], [])
        self.assertNotIn('error_type', value['economy'])


if __name__ == '__main__':
    unittest.main()
