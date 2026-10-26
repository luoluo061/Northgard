"""Own-operation reader guards and privacy boundaries, using a fake read-only HL heap."""
from pathlib import Path
import json
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import operation_capture as oc


class Heap:
    def __init__(self):
        self.fields = {
            1: {'__uid':31,'aiJob':0,'world':2,'res':3,'goatFeastCostReduction':0.0,
                'teamLeader':0,'hunt':11,'pack':12},
            2: {},
            3: {'feastCount':2,'lastFeast':90.0,'freeFeast':0,'freeFeastCount':0,
                'freeFeastTime':0.0,'feastInstigator':1},
            4: {'time':100.0,'zones':10,'me':1},
            5: {'id':7,'owner':1,'colonizeBy':1,'colonize':0.4,'costReduction':0},
            6: {'id':999,'owner':88,'colonizeBy':88,'colonize':0.8,'costReduction':0},
            7: {'zone':5,'__uid':41,'kind':'House','wasFree':False},
            8: {'zone':6,'__uid':42,'kind':'Forge','wasFree':False},
            11: {'feastCostReduction':0.0}, 12:{'livingLegendStacks':0},
        }
        self.types = {1:'ent.Player',2:'ent.player.WorldComponent',3:'ent.player.ResourcesComponent',
                      11:'ent.player.ArrowsComponent',12:'ent.player.PackComponent'}
    def field(self, ptr, name): return self.fields[ptr][name]
    def object_type(self, ptr): return SimpleNamespace(name=self.types.get(ptr,'not-needed'), kind=11)
    def dynamic(self, ptr): return ptr


class OperationsTests(unittest.TestCase):
    def run_capture(self, heap=None, **kwargs):
        heap = heap or Heap()
        prices = {'colonizedPrice':{7:[{'resource':'Food','amount':80}]},'extraColonizedPrice':{}}
        with patch.object(oc,'objects',lambda h,p,limit=4096:[5,6]), \
             patch.object(oc,'string',lambda h,p:p), \
             patch.object(oc,'_price_map',lambda h,w,f,k:prices[f]), \
             patch.object(oc,'_simple_own_tech_flags',return_value={'TrialFaith':False,'PaladinCall':False}), \
             patch.object(oc,'_building_cost',return_value=[{'resource':'Wood','amount':50}]):
            return oc.capture_operations(heap,1,4,known_zone_ids=[7],
                owned_building_pointers=kwargs.pop('owned_building_pointers',[(7,7)]),
                identity={'session_key':'s','capture_run_id':'r','capture_segment_id':'r:1'},
                profile=kwargs.pop('profile',{'hlboot_sha256':oc.VERIFIED_HASH,
                                             'data_cdb_sha256':oc.VERIFIED_CDB_HASH}),**kwargs)
    def test_verified_own_state_and_actual_stored_cost(self):
        row = self.run_capture()
        self.assertEqual(row['status'],'ok')
        self.assertEqual(row['local_player_entity_uid'],31)
        self.assertEqual(row['colonizations'],[{'zone_id':7,'progress':0.4,
            'started_by_self':True,'cost_reduction':None,'paid_cost':[{'resource':'Food','amount':80}],
            'extra_paid_cost':None,'paid_cost_record_status':'stored_record',
            'extra_paid_cost_record_status':'no_record'}])
        self.assertEqual(row['building_costs'][0]['cost'],[{'resource':'Wood','amount':50}])
        self.assertFalse(row['complete_transaction_ledger'])
        self.assertFalse(row['atomic'])
        self.assertEqual(row['capture_segment_id'],'r:1')
    def test_unknown_zone_and_foreign_building_do_not_escape(self):
        row = self.run_capture(owned_building_pointers=[(7,7),(8,999)])
        serialized = json.dumps(row)
        self.assertNotIn('999',serialized)
        self.assertNotIn('42',serialized)
        self.assertEqual(row['status'],'partial')
        self.assertIn('own_building_ownership_not_verified',row['reason_codes'])
    def test_bad_build_fails_closed(self):
        row = self.run_capture(profile={'hlboot_sha256':'other'})
        self.assertEqual(row['status'],'unknown')
        self.assertEqual(row['reason_codes'],['unverified_build'])
        self.assertEqual(row['building_costs'],[])
    def test_missing_optional_field_preserves_other_components(self):
        h = Heap(); del h.fields[3]['lastFeast']
        row = self.run_capture(h)
        self.assertEqual(row['status'],'partial')
        self.assertIsNone(row['feast']['last_feast'])
        self.assertEqual(row['feast']['feast_count'],2)
        self.assertEqual(row['colonizations'][0]['zone_id'],7)
    def test_ai_identity_rejected(self):
        h=Heap();h.fields[1]['aiJob']=900
        row=self.run_capture(h)
        self.assertEqual(row['status'],'unknown')
        self.assertEqual(row['reason_codes'],['unverified_local_human_player'])
    def test_state_me_must_match(self):
        h=Heap();h.fields[4]['me']=9
        row=self.run_capture(h)
        self.assertEqual(row['status'],'unknown')
        self.assertEqual(row['reason_codes'],['unverified_local_human_player'])
    def test_modifiers_are_explicit_and_team_pointer_does_not_escape(self):
        h=Heap();h.fields[1]['teamLeader']=987654321;h.fields[12]['livingLegendStacks']=2
        row=self.run_capture(h)
        self.assertFalse(row['feast']['team_leader_is_self'])
        self.assertTrue(row['feast']['team_leader_present'])
        self.assertEqual(row['feast']['living_legend_cost_reduction'],0.2)
        self.assertEqual(row['feast']['hunt_cost_reduction'],0)
        self.assertIs(row['feast']['has_relic_goat'],False)
        self.assertIs(row['feast']['has_relic_chalice'],False)
        self.assertNotIn('987654321',json.dumps(row))
    def test_missing_static_data_fingerprint_refuses_legend_formula(self):
        row=self.run_capture(profile={'hlboot_sha256':oc.VERIFIED_HASH})
        self.assertIsNone(row['feast']['living_legend_cost_reduction'])
        self.assertEqual(row['status'],'partial')
    def test_invalid_known_ids_rejected_without_output(self):
        h=Heap()
        row=oc.capture_operations(h,1,4,known_zone_ids=[True],owned_building_pointers=[],
                                  profile={'hlboot_sha256':oc.VERIFIED_HASH})
        self.assertEqual(row['status'],'unknown')
        self.assertEqual(row['colonizations'],[])
    def test_no_exception_text_or_addresses_leak(self):
        h=Heap()
        def broken(ptr,name): raise OSError('private-path/account/0xDEADBEEF')
        h.field=broken
        row=self.run_capture(h)
        self.assertNotIn('private-path',json.dumps(row))
        self.assertNotIn('DEADBEEF',json.dumps(row))


class EnumCostTests(unittest.TestCase):
    def test_known_enum_normalizes_numeric_cost(self):
        h=SimpleNamespace(object_type=lambda p:SimpleNamespace(name='enum:ent.vars.ColonizeType',kind=18))
        with patch.object(oc,'enum_values',return_value=('ColWithFood',[(6,150.0)])):
            self.assertEqual(oc._colonize_cost(h,4),[{'resource':'Food','amount':150.0}])
    def test_cooldown_branch_not_a_zero_cost_payment(self):
        h=SimpleNamespace(object_type=lambda p:SimpleNamespace(name='enum:ent.vars.ColonizeType',kind=18))
        with patch.object(oc,'enum_values',return_value=('ColWithDominion',[])):
            with self.assertRaises(ValueError): oc._colonize_cost(h,4)
    def test_unrelated_enum_rejected(self):
        h=SimpleNamespace(object_type=lambda p:SimpleNamespace(name='enum:Wrong',kind=18))
        with self.assertRaises(ValueError): oc._colonize_cost(h,4)


class IntMapTests(unittest.TestCase):
    def heap(self,mutate=False):
        buffers={100:struct.pack('<QQQQQiiiii4x',200,300,400,500,0,0,1,1,1,1),
                 200:b'\0',300:b'\xff',400:struct.pack('<i',7),500:struct.pack('<Q',600)}
        reads={}
        def read(ptr,size):
            reads[ptr]=reads.get(ptr,0)+1
            if mutate and ptr==500 and reads[ptr]>1:return struct.pack('<Q',999)
            return buffers[ptr]
        return SimpleNamespace(object_type=lambda p:SimpleNamespace(name='haxe.ds.IntMap'),
                               field=lambda p,n:100,read=read)
    def test_map_pointer_entries_and_storage_guard(self):
        self.assertEqual(oc._integer_map_pointers(self.heap(),1),{7:600})
        with self.assertRaises(ValueError):oc._integer_map_pointers(self.heap(True),1)


class WrappedOperationMapTests(unittest.TestCase):
    def heap(self, virtual=True):
        fields = {(1, 'tech'): 11, (1, 'owl'): 0,
                  (11, 'allFountainResearches'): 0, (11, 'researchedTechs'): 12,
                  (12, 'map'): 13 if virtual else 14, (14, 'h'): 1400,
                  (2, 'colonizedPrice'): 22, (22, 'map'): 23 if virtual else 24,
                  (24, 'h'): 2400}
        types = {11: ('ent.player.TechComponent', 11), 12: ('hxbit.MapData', 11),
                 13: ('virtual', 15), 14: ('haxe.ds.StringMap', 11),
                 22: ('hxbit.MapData', 11), 23: ('virtual', 15), 24: ('haxe.ds.IntMap', 11)}
        values = {'TrialFaith': True}
        h = SimpleNamespace(field=lambda p,n: fields[p,n], ptr=lambda p: {21:14,31:24}[p],
            object_type=lambda p: SimpleNamespace(name=types[p][0], kind=types[p][1]),
            string_map=lambda p: dict(values))
        return h, fields, types, values

    def test_price_map_unwraps_but_only_decodes_known_zone_costs(self):
        for virtual in (False, True):
            with self.subTest(virtual=virtual):
                h, _, _, _ = self.heap(virtual)
                with patch.object(oc, '_integer_map_pointers', return_value={7: 600, 999: 666}), \
                     patch.object(oc, '_colonize_cost', side_effect=lambda h,p: [{'resource':'Food','amount':80}] if p==600 else self.fail('Foreign zone decoded')):
                    self.assertEqual(oc._price_map(h, 2, 'colonizedPrice', {7}), {7:[{'resource':'Food','amount':80}]})

    def test_normal_tech_flags_unwrap_virtual_or_concrete_map(self):
        for virtual in (False, True):
            with self.subTest(virtual=virtual):
                h, _, _, _ = self.heap(virtual)
                self.assertEqual(oc._simple_own_tech_flags(h,1,4), {'TrialFaith':True,'PaladinCall':False})

    def test_wrapper_mutation_or_null_backing_rejects_price_map(self):
        h, fields, _, _ = self.heap()
        def mutate(*args):
            fields[22, 'map'] = 24
            return {7: 600}
        with patch.object(oc, '_integer_map_pointers', side_effect=mutate), \
             patch.object(oc, '_colonize_cost', return_value=[]), self.assertRaises(ValueError):
            oc._price_map(h, 2, 'colonizedPrice', {7})
        h, fields, _, _ = self.heap()
        fields[22, 'map'] = 0
        with self.assertRaises(ValueError):
            oc._price_map(h, 2, 'colonizedPrice', {7})

    def test_tech_value_or_wrapper_changes_do_not_prove_absence(self):
        for mutation in ('values', 'wrapper', 'wrong_type'):
            with self.subTest(mutation=mutation):
                h, fields, types, values = self.heap()
                def changing(p):
                    result = dict(values)
                    if mutation == 'values': values['PaladinCall'] = True
                    elif mutation == 'wrapper': fields[12, 'map'] = 14
                    else: types[14] = ('haxe.ds.IntMap', 11)
                    return result
                h.string_map = changing
                with self.assertRaises(ValueError):
                    oc._simple_own_tech_flags(h,1,4)


if __name__=='__main__':unittest.main()
