"""Minimal local definition index; fixtures never access a game process."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import inspect_map_catalog as catalog

PROFILE = {'hlboot_sha256': catalog.HLBOOT_SHA256,
           'data_cdb_sha256': catalog.DATA_CDB_SHA256}


def fixture():
    return {'sheets': [
        {'name': 'unit', 'separators': [{'id': 'Wolf', 'title': 'Foes'}], 'lines': [
            {'id': 'Wolf', 'flags': 0, 'types': 130, 'props': {'canBefriend': 'Boar'},
             'gfx': {'file': 'SECRET.png'}, 'texts': {'name': 'SECRET'},
             'phases': [{'model': 'SECRET', 'lootTable': [{'res': 'Meat', 'qty': 15,
                         'onlyForClan': 'Wolf', 'gfx': 'SECRET'}]}]},
            {'id': 'BearMaiden', 'flags': 1, 'props': {'onlyForClans': [{'clan': 'Bear'}]}},
            {'id': 'Deer', 'flags': 2, 'props': {}}]},
        {'name': 'building', 'separators': [], 'lines': [
            {'id': 'Treasure', 'props': {'onlyForClans': [{'clan': 'Eagle'}], 'fixedAssign': 2},
             'gfx': 'SECRET', 'texts': {'desc': 'SECRET'}},
            {'id': 'FountainFame', 'props': {'canProduce': [{'res': 'Lore'}]}},
            {'id': 'Stones', 'flags': 2056, 'props': {'canProduce': [{'res': 'Stone'}]},
             'gen': {'flags': 0, 'probaAppear': 0, 'asset': 'SECRET'}}]},
        {'name': 'neutral', 'lines': [{'id': 'DwarfFaction', 'unit': 'Dwarf', 'place': 'DwarfHouse',
            'otherBuildings': [], 'props': {'giftUnit': 'DwarfChampion', 'texture': 'SECRET'},
            'befriendGift': [{'res': 'Fame', 'qty': 50}],
            'eradicationReward': [{'res': 'Fame', 'qty': -100}, {'res': 'Food', 'qty': 0}, {'res': 'Lore'}],
            'alignmentSteps': [{'step': 0, 'desc': 'SECRET', 'icon': {'file': 'SECRET'}}],
            'texts': {'name': 'SECRET'}}]}]}


class MapCatalogTests(unittest.TestCase):
    def build(self, value=None):
        return catalog.build_catalog(value or fixture(), PROFILE)

    def test_minimal_projection_removes_assets_texts_and_paths(self):
        out = self.build()
        text = json.dumps(out)
        for forbidden in ('SECRET', 'gfx', 'texts', 'texture', 'model'):
            self.assertNotIn(forbidden, text)
        self.assertFalse(out['is_live_map'])
        self.assertFalse(out['is_capture_or_submission'])
        self.assertFalse(out['runtime_scope_expanded'])
        self.assertEqual(out['data_semantics'], 'configured_not_actual')

    def test_negative_zero_and_missing_quantities_are_distinct(self):
        rewards = self.build()['neutrals'][0]['eradicationReward']
        self.assertEqual([rewards[0]['qty'], rewards[1]['qty']], [-100, 0])
        self.assertNotIn('qty', rewards[2])
        stone = self.build()['buildings'][2]
        self.assertEqual(stone['gen'], {'flags': 0, 'probaAppear': 0})
        self.assertNotIn('fixedAssign', stone['props'])

    def test_duplicate_empty_heading_keeps_compiled_group_number(self):
        data = fixture()
        data['sheets'][0]['separators'] = [
            {'id': 'Wolf', 'title': 'Empty campaign group'},
            {'id': 'Wolf', 'title': 'Foes'}, {'id': 'Deer', 'title': 'Passive'}]
        out = self.build(data)
        self.assertEqual([r['group_index'] for r in out['units']], [1, 1, 2])
        self.assertEqual(len(out['unit_groups']), 3)

    def test_candidate_groups_are_not_an_expanded_runtime_whitelist(self):
        data = fixture()
        data['sheets'][0]['separators'] = [
            {'id': 'Prefix' + str(i), 'title': 'Group' + str(i)} for i in range(35)] + [
            {'id': 'BearMaiden', 'title': 'Foe heroes'}, {'id': 'Wolf', 'title': 'Foes'},
            {'id': 'Unused37', 'title': '37'}, {'id': 'Unused38', 'title': '38'},
            {'id': 'Unused39', 'title': '39'}, {'id': 'Deer', 'title': 'Passive'}]
        # Row order is the source of group membership, not the sort order of IDs.
        data['sheets'][0]['lines'] = [data['sheets'][0]['lines'][1],
            data['sheets'][0]['lines'][0], data['sheets'][0]['lines'][2]]
        out = self.build(data)
        self.assertEqual([u['group_index'] for u in out['units']], [35, 36, 40])
        self.assertTrue(all(u['neutral_candidate_hint'] for u in out['units']))
        self.assertEqual(out['units'][0]['runtime_support'], 'catalog_only')
        self.assertEqual(out['units'][1]['runtime_support'], 'implemented_not_live_validated')

    def test_fame_in_id_is_not_a_fame_reward_classification(self):
        out = self.build()
        self.assertEqual(out['summary']['building_fame_reference_rows'], 0)
        self.assertEqual(out['buildings'][1]['props']['canProduce'], [{'res': 'Lore'}])

    def test_treasure_keeps_clan_conditions_without_generic_reward_invention(self):
        value = self.build()['buildings'][0]
        self.assertEqual(value['id'], 'Treasure')
        self.assertEqual(value['props']['onlyForClans'], [{'clan': 'Eagle'}])
        self.assertNotIn('winRes', value['props'])
        self.assertEqual(value['mode_eligibility'], 'not_evaluated')

    def test_values_strip_expressions_comments_and_upgrade_contents(self):
        data = fixture()
        data['sheets'][1]['lines'][1]['values'] = [
            {'id': 5, 'val': 0, 'comments': 'SECRET comment',
             'dyn': [{'expression': 'SECRET expression'}],
             'upgradedVals': [{'val': 99, 'comments': 'SECRET override'}],
             'props': {'res': 'Lore', 'type': 1, 'clanType': 0, 'anywhere': False,
                       'notForClans': [{'clan': 'Eagle', 'asset': 'SECRET'}],
                       'description': 'SECRET'}}]
        building = self.build(data)['buildings'][1]
        self.assertEqual(building['values_semantics'], 'configured_reference_not_effective')
        self.assertEqual(building['values'], [{'id': 5, 'val': 0,
            'props': {'res': 'Lore', 'type': 1, 'clanType': 0, 'anywhere': False,
                      'notForClans': [{'clan': 'Eagle'}]},
            'has_dynamic_definition': True, 'has_upgrade_overrides': True}])
        for forbidden in ('SECRET', 'comments', 'expression', 'upgradedVals'):
            self.assertNotIn(forbidden, json.dumps(building))

    def test_static_values_preserve_zero_and_missing_without_claiming_effective_stock(self):
        data = fixture()
        data['sheets'][1]['lines'][1]['values'] = [
            {'id': 5, 'val': 310, 'dyn': [], 'upgradedVals': []},
            {'id': 11, 'val': 0}, {'id': 12}]
        building = self.build(data)['buildings'][1]
        self.assertEqual(building['values_semantics'], 'configured_reference_not_effective')
        self.assertEqual([x.get('val') for x in building['values']], [310, 0, None])
        self.assertNotIn('val', building['values'][2])
        self.assertTrue(all(not x['has_dynamic_definition'] and not x['has_upgrade_overrides']
                            for x in building['values']))
        self.assertNotIn('stock', building)
        self.assertNotIn('effective', building)
        self.assertEqual(building['mode_eligibility'], 'not_evaluated')

    def test_values_are_limited_to_named_buildings_and_created_by_remains_an_id(self):
        data = fixture()
        for row in data['sheets'][1]['lines']:
            row['values'] = [{'id': 5, 'val': 310, 'comments': 'SECRET'}]
        data['sheets'][1]['lines'].append({'id': 'TownhallRuin',
            'gen': {'createdBy': 'NiflheimPortal', 'comments': 'SECRET'},
            'values': [{'id': 5, 'val': 999}]})
        buildings = self.build(data)['buildings']
        self.assertNotIn('values', buildings[0])  # Treasure is not CursedTreasure.
        self.assertIn('values', buildings[1])
        self.assertNotIn('values', buildings[2])
        self.assertNotIn('values', buildings[3])
        self.assertEqual(buildings[3]['gen'], {'createdBy': 'NiflheimPortal'})
        self.assertFalse(self.build(data)['runtime_scope_expanded'])

    def test_profile_mismatch_is_rejected(self):
        for key in PROFILE:
            profile = dict(PROFILE); profile[key] = '0' * 64
            with self.assertRaises(ValueError):
                catalog.build_catalog(fixture(), profile)

    def test_installation_mismatch_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'new' / 'catalog.json'
            with patch.object(catalog, 'choose_installation', return_value=Path(folder)), \
                    patch.object(catalog, 'sha256_file', return_value='0' * 64), \
                    patch.object(catalog, 'read_pak_index') as pak_read, \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    catalog.main(['--out', str(target)])
            self.assertFalse(target.parent.exists())
            pak_read.assert_not_called()

    def test_rule_mismatch_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'new' / 'catalog.json'
            with patch.object(catalog, 'choose_installation', return_value=Path(folder)), \
                    patch.object(catalog, 'sha256_file', return_value=PROFILE['hlboot_sha256']), \
                    patch.object(catalog, 'read_pak_index', return_value={'entries': [{'path': 'data.cdb'}]}), \
                    patch.object(catalog, 'read_entry', return_value=b'unsupported'), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    catalog.main(['--out', str(target)])
            self.assertFalse(target.parent.exists())

    def test_search_is_case_insensitive_id_only(self):
        out = self.build()
        self.assertEqual([x['id'] for x in catalog.search_catalog(out, 'fOUNtain')['buildings']], ['FountainFame'])
        self.assertEqual(catalog.search_catalog(out, 'Eagle')['buildings'], [])
        with self.assertRaises(ValueError):
            catalog.search_catalog(out, ' ')

    def test_output_is_exclusive_and_cannot_modify_game_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); game = root / 'game'; game.mkdir()
            target = root / 'local' / 'catalog.json'
            out = self.build()
            catalog.write_catalog(out, target, installation=game)
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')), out)
            with self.assertRaises(FileExistsError):
                catalog.write_catalog(out, target, installation=game)
            with self.assertRaises(ValueError):
                catalog.write_catalog(out, game / 'catalog.json', installation=game)
            self.assertFalse((game / 'catalog.json').exists())

    def test_default_cli_prints_summary_not_all_rows_and_out_is_local_only(self):
        out = self.build()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'catalog.json'; stream = io.StringIO()
            with patch.object(catalog, 'load_installed_catalog', return_value=(out, Path(folder) / 'game')), \
                    contextlib.redirect_stdout(stream):
                self.assertEqual(catalog.main(['--out', str(target)]), 0)
            receipt = json.loads(stream.getvalue())
            self.assertTrue(receipt['local_only']); self.assertTrue(receipt['output_written'])
            self.assertEqual(receipt['summary']['neutral_rows'], 1)
            self.assertNotIn('units', receipt); self.assertNotIn('matches', receipt)
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')), out)


if __name__ == '__main__':
    unittest.main()
