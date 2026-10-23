"""Synthetic coordination templates, never a real match or player identity."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from create_match_kit import create_kit, shared_uuid


class MatchKitTests(unittest.TestCase):
    def test_six_seats_share_one_random_match_and_keep_consent_false(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'kit'
            result = create_kit(output)
            self.assertFalse(result['game_launched'])
            self.assertFalse(result['capture_started'])
            self.assertFalse(result['upload_performed'])
            seats = set()
            for name in result['metadata_templates']:
                row = json.loads((output / name).read_text(encoding='utf-8'))
                self.assertFalse(row['consent_to_share'])
                self.assertEqual(row['mode'], 'multiplayer')
                self.assertIsNone(row['clan'])
                link = row['match_link']
                self.assertEqual(link['shared_match_id'], result['shared_match_id'])
                seats.add((link['team_label'], link['player_slot']))
            self.assertEqual(seats, {(t, s) for t in ('A', 'B') for s in (1, 2, 3)})
            text = (output / 'commands.txt').read_text(encoding='utf-8')
            self.assertEqual(text.count('python collector/collect.py'), 6)
            self.assertNotIn('git push', text)

    def test_existing_kit_is_preserved_and_invalid_ids_create_no_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'kit'
            create_kit(output)
            before = (output / 'commands.txt').read_bytes()
            with self.assertRaises(ValueError):
                create_kit(output)
            self.assertEqual((output / 'commands.txt').read_bytes(), before)
            bad = Path(folder) / 'bad'
            with self.assertRaises(ValueError):
                create_kit(bad, shared_match_id='../not-an-id')
            self.assertFalse(bad.exists())

    def test_ids_are_canonical_v4_and_unique_for_different_matches(self):
        with tempfile.TemporaryDirectory() as folder:
            first = create_kit(Path(folder) / 'one')['shared_match_id']
            second = create_kit(Path(folder) / 'two')['shared_match_id']
            self.assertNotEqual(first, second)
            self.assertEqual(shared_uuid(first), first)
        for invalid in ('', '123', '00000000-0000-1000-8000-000000000000',
                        '12345678-1234-4234-8234-123456789ABC'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                shared_uuid(invalid)


if __name__ == '__main__':
    unittest.main()
