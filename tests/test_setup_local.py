import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import setup_local as setup
from local_rules import verify_generated_rules
import hashlib


class SetupTests(unittest.TestCase):
    def test_generated_rule_tampering_or_changed_profile_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'research/runtime').mkdir(parents=True)
            (root/'research/static').mkdir()
            (root/'.local').mkdir()
            profile={'hlboot_sha256':'a'*64,'data_cdb_sha256':'b'*64}
            (root/'research/runtime/build_profile.json').write_text(json.dumps(profile),encoding='utf-8')
            hashes={}
            for name in ['unit.json','resource.json','labor_rules.json']:
                (root/'research/static'/name).write_bytes(b'{}')
                hashes[name]=hashlib.sha256(b'{}').hexdigest()
            (root/'.local/rules-manifest.json').write_text(json.dumps({'schema_version':1,**profile,'files':hashes}),encoding='utf-8')
            self.assertTrue(verify_generated_rules(root))
            (root/'research/static/unit.json').write_bytes(b'{"changed":true}')
            self.assertFalse(verify_generated_rules(root))

    def fixture(self):
        return {'sheets':[{'name':'unit','lines':[{'id':'Villager','types':4,'texts':{'name':'private text'},'gfx':{'file':'asset.png'},
            'produce':[{'r':'Food','q':.4,'req':{},'secret':'DROP'}],'props':{}}]},
            {'name':'resource','lines':[{'id':'Food','isProductible':True,'props':{},'gfx':'DROP'}]},
            {'name':'building','lines':[{'id':'House','values':[{'id':2,'val':1.2,'comments':'DROP'}]}]}]}

    def test_rules_keep_required_numeric_inputs_without_assets(self):
        out=setup.derive_rules(self.fixture(),{'hlboot_sha256':'a'*64,'data_cdb_sha256':'b'*64})
        self.assertEqual(out['unit.json']['rows'][0]['types'],4)
        self.assertEqual(out['resource.json']['rows'][0]['_row_index'],0)
        self.assertEqual(out['labor_rules.json']['tables']['unit'][0]['produce'][0]['q'],.4)
        self.assertEqual(out['labor_rules.json']['tables']['building'][0]['values'][0]['id'],2)
        text=json.dumps(out)
        self.assertNotIn('DROP',text)
        self.assertNotIn('asset.png',text)
        self.assertNotIn('private text',text)

    def test_unknown_fields_do_not_pass_recursive_projection(self):
        self.assertEqual(setup.pick({'ok':{'n':2,'password':'DROP'},'extra':'DROP'}, {'ok':{'n':True}}),{'ok':{'n':2}})

    def test_unsupported_bytecode_leaves_rules_uncreated(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'repo';game=Path(directory)/'game'
            (root/'research/runtime').mkdir(parents=True);game.mkdir()
            (root/'research/runtime/build_profile.json').write_text(json.dumps({'hlboot_sha256':'a'*64}),encoding='utf8')
            (game/'hlboot.dat').write_bytes(b'wrong build')
            with self.assertRaisesRegex(ValueError,'Unsupported game bytecode'):
                setup.setup(game,root)
            self.assertFalse((root/'research/static').exists())

    def test_ambiguous_installations_require_explicit_selection(self):
        with patch.object(setup,'ROOT',Path(tempfile.gettempdir())/'does-not-exist-northgard-test'),patch.object(setup,'steam_candidates',return_value=[Path('a'),Path('b')]):
            with self.assertRaisesRegex(ValueError,'Pass --game-dir'):
                setup.choose_installation()


if __name__=='__main__':
    unittest.main()
