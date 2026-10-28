"""Recorded-view checks: no time travel, stale fill, or missing-to-zero display."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import show_known_zones as report
from companion.tests.test_known_zone_details import fixture
from companion.core.player_observation import project_player_observation


def row(when, *, session='session', segment='segment'):
    return {'status': 'ok', 'session_key': session, 'capture_run_id': 'run',
            'capture_segment_id': segment, 'game_time_seconds': when}


class KnownZoneReportTests(unittest.TestCase):
    def test_default_text_explains_zone_partial_reasons(self):
        component, parent = fixture()
        zone = component['zones'][1]
        zone['resources_status'] = 'partial'
        zone['resource_reason_codes'] = ['known_zone_shoal_enablement_unsupported']
        for resource in zone['resources']:
            resource['remaining_total_raw'] = resource['modeled_ui_remaining_total'] = None
        zone['wildlife_status'] = 'partial'
        zone['wildlife'] = [{'kind': 'Wolf', 'count': 2}]
        zone['wildlife_reason_codes'] = ['known_zone_grass_visibility_unsupported']
        parent['known_zone_details'] = component
        value = report.build_report(project_player_observation(parent))
        self.assertEqual(value['reason_codes'], [])
        text = report.format_report(value)
        self.assertIn('鱼源的海鱼或启用状态尚未确认', text)
        self.assertIn('known_zone_shoal_enablement_unsupported', text)
        self.assertIn('草丛隐藏规则暂未覆盖', text)

    def test_real_projection_to_report_preserves_mine_identity_and_meaning(self):
        component, parent = fixture()
        parent['known_zone_details'] = component
        observation = project_player_observation(parent)
        value = report.build_report(observation)
        mineral = value['zones'][1]['resources'][0]
        self.assertEqual(mineral['deposits'][0]['natural_object_uid'], 1001)
        self.assertEqual(mineral['remaining_total_raw'], 20.5)
        self.assertEqual(mineral['modeled_ui_remaining_total'], 21)
        self.assertIsNone(mineral['reference_properties']['fixed_assignment_slots'])
        text = report.format_report(value)
        self.assertIn('剩余总量 20.5', text)
        self.assertIn('狼：2', text)
        self.assertIn('符文石', text)
        self.assertIn('矿藏式剩余量不适用', text)

    def select(self, rows, **kwargs):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'observations.ndjson'
            path.write_text(''.join(json.dumps(v) + '\n' for v in rows), encoding='utf-8')
            return report.select_observation(path, **kwargs)

    def test_calendar_matches_game_months_and_thirds(self):
        expected = {0: 'A800年3月初', 20: 'A800年3月中', 40: 'A800年3月末',
                    60: 'A800年4月初', 540: 'A800年12月初',
                    720: 'A801年3月初', 740: 'A801年3月中'}
        for seconds, label in expected.items():
            self.assertEqual(report.game_calendar(seconds), label)
        for value in (-1, True, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                report.game_calendar(value)

    def test_as_of_never_reads_future_component(self):
        before, future = row(10), row(30)
        before['known_zone_details'] = {'status': 'unavailable'}
        future['known_zone_details'] = {'status': 'partial', 'secret_future_marker': 1}
        selected = self.select([before, future], at=20)
        self.assertEqual(selected, before)
        self.assertNotIn('secret_future_marker', str(selected))

    def test_latest_missing_component_does_not_reuse_previous_success(self):
        old, current = row(1), row(2)
        old['known_zone_details'] = {'status': 'partial', 'zones': ['old']}
        selected = self.select([old, current])
        value = report.build_report(selected)
        self.assertEqual(value['component_status'], 'missing')
        self.assertEqual(value['zones'], [])
        self.assertIn('not_live_world_state', value['report_kind'])

    def test_new_session_does_not_reuse_old_map(self):
        old, new = row(100), row(0, session='new-session')
        self.assertEqual(self.select([old, new]), new)
        self.assertEqual(self.select([old, new], session='session'), old)

    def test_query_before_latest_segment_has_no_fallback(self):
        with self.assertRaises(ValueError):
            self.select([row(10), row(50, segment='new-segment')], at=20)

    def test_pause_keeps_last_observation_at_same_game_clock(self):
        first, last = row(10), row(10)
        last['sequence'] = 2
        self.assertEqual(self.select([first, last], at=10), last)

    def test_rollback_without_new_segment_rejected(self):
        with self.assertRaises(ValueError):
            self.select([row(10), row(9)])

    def test_incomplete_last_write_is_not_a_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'observations.ndjson'
            path.write_text(json.dumps(row(10)) + '\n{"status":', encoding='utf-8')
            with self.assertRaises(ValueError):
                report.select_observation(path)

    def test_untrusted_component_is_not_rendered(self):
        source = row(10)
        source['known_zone_details'] = {'status': 'partial', 'owner': 'private-player-name'}
        with self.assertRaises(ValueError):
            report.build_report(source)

    def test_unknown_amount_and_wildlife_not_formatted_as_zero(self):
        value = report.build_report(row(10))
        value['component_status'] = 'partial'
        value['zones'] = [{'zone_id': 1, 'resources_status': 'partial',
            'wildlife_status': 'partial', 'resources': [{'kind': 'Stones', 'object_count': 1,
                'remaining_total_raw': None, 'deposits': [{'next_extraction_progress_raw': None}]}],
            'wildlife': [{'kind': 'Wolf', 'count': None}]}]
        text = report.format_report(value)
        self.assertIn('剩余总量 未知', text)
        self.assertIn('狼：未知', text)
        self.assertNotIn('狼：0', text)
        self.assertNotIn('剩余总量 0', text)


if __name__ == '__main__':
    unittest.main()
