"""Exercise real ActionProbe wiring with the existing invented-heap fixture."""
import struct
import unittest
from unittest.mock import patch

import action_capture as ac
import test_optional_components as fixtures


class KnownZoneIntegrationTests(unittest.TestCase):
    def sample(self):
        return fixtures.OptionalComponentsTests().sample()

    def test_component_is_called_by_normal_action_sample(self):
        component = {'schema_version': 1, 'status': 'partial', 'zones': []}
        with patch.object(ac, 'capture_known_zone_details', return_value=component) as call:
            row = self.sample()
        self.assertEqual(row['status'], 'ok')
        self.assertIs(row['known_zone_details'], component)
        args, kwargs = call.call_args
        self.assertEqual(args[1:], (4, 3))
        self.assertEqual(kwargs['known_zone_ids'], [])
        self.assertTrue({'session_key', 'capture_run_id', 'capture_segment_id'}
                        .issubset(kwargs['identity']))
        self.assertEqual(kwargs['game_context'], row['game_context'])

    def test_optional_reader_failures_keep_core_and_other_components(self):
        for error in (OSError, ValueError, KeyError, TypeError, struct.error,
                      OverflowError, RuntimeError):
            with self.subTest(error=error.__name__):
                with patch.object(ac, 'capture_known_zone_details',
                                  side_effect=error('private C:/secret 0xDEADBEEF')):
                    row = self.sample()
                self.assertEqual(row['status'], 'ok')
                self.assertEqual(row['economy']['status'], 'ok')
                self.assertEqual(row['map_observation']['status'], 'ok')
                details = row['known_zone_details']
                self.assertEqual(details['status'], 'unavailable')
                self.assertEqual(details['scope'], 'no_known_zone_details')
                self.assertEqual(details['zones'], [])
                self.assertEqual(details['reason_codes'],
                                 ['known_zone_details_component_read_failed'])
                self.assertNotIn('secret', str(details))
                self.assertNotIn('DEADBEEF', str(details))

    def test_unsupported_context_is_preserved_without_fabricated_game_data(self):
        component = {'schema_version': 1, 'status': 'unsupported_context',
                     'scope': 'no_known_zone_details', 'zones': []}
        with patch.object(ac, 'capture_known_zone_details', return_value=component):
            row = self.sample()
        self.assertEqual(row['known_zone_details'], component)
        self.assertEqual(row['status'], 'ok')


if __name__ == '__main__':
    unittest.main()
