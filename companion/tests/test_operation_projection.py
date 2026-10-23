import copy
import importlib.util
import json
from pathlib import Path
import unittest

SPEC=importlib.util.spec_from_file_location('operation_projection',Path(__file__).resolve().parents[1]/'core/operation_projection.py')
OP=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(OP)


def snapshot():
    return {'schema_version':2,'status':'ok','session_key':'match-A','capture_run_id':'run-A',
        'capture_segment_id':'run-A:1','local_player_entity_uid':31,
        'local_player_identity':{'verified':True,'role':'player','entity_uid':31,'is_ai':False,'in_player_list':True},
        'game_time_seconds':120.5,'owned_zone_ids':[87],'discovered_zone_ids':[87,88],
        'buildings':[{'uid':20,'zone_id':87,'kind':'Construction'}],'clan':'Bear',
        'game_context':{'mode':{'kind':'Singleplayer','args':[{'kind':'FreeForAll','args':[]}]},
                        'bifrost_data_present':False,'editor_present':False}}


def operations():
    parent=snapshot()
    feast={'instigator_is_self':True,'team_leader_present':False,'team_leader_is_self':True,
        'has_relic_goat':False,'has_relic_chalice':False,'has_trial_faith':False,
        'feast_count':4,'free_feast':0,'free_feast_count':0,'living_legend_stacks':0,
        'last_feast':90.0,'free_feast_time':0.0,'goat_cost_reduction':0.0,
        'hunt_cost_reduction':0.0,'living_legend_cost_reduction':0.0}
    return {'schema_version':1,'source':OP.SOURCE,'status':'ok',
        **{k:parent[k] for k in OP.IDENTITIES},'local_player_entity_uid':31,'game_time_seconds':120.5,
        'time_before':120.5,'time_after':120.5,'sample_span_game_seconds':0.0,
        'atomic':False,'complete_transaction_ledger':False,'unknown_fields':[],'reason_codes':[],
        'feast':feast,'colonizations':[{'zone_id':88,'progress':0.25,'started_by_self':True,
            'cost_reduction':None,'paid_cost':[{'resource':'Food','amount':530}],
            'paid_cost_record_status':'stored_record','extra_paid_cost':None,
            'extra_paid_cost_record_status':'no_record'}],
        'building_costs':[{'uid':20,'zone_id':87,'kind':'Construction','was_free':False,
            'cost':[{'resource':'Wood','amount':300},{'resource':'Money','amount':300},{'resource':'Stone','amount':15}],
            'cost_record_status':'stored_record'}]}


def with_quote(row):
    row['feast_quote']={'schema_version':1,'status':'verified','source':OP.QUOTE_SOURCE,
        'build_evidence':{'hlboot_sha256':OP.HLBOOT_SHA256,'data_cdb_sha256':OP.DATA_CDB_SHA256},
        'input_evidence':{k:row['feast'][k] for k in OP.QUOTE_INPUTS},
        'cost':[{'resource':'Food','amount':510}],'formula_branch':'paid_feast_zero_modifiers',
        'formula':'MUST_NOT_COPY','native_function':{'name':'ignore','findex':13528},'private_path':'DO_NOT_COPY'}
    return row


class OperationProjectionTests(unittest.TestCase):
    def test_valid_state_preserved_with_stone_and_record_tristate(self):
        row=operations();parent=snapshot();original=copy.deepcopy(row)
        out=OP.project_operations(row,parent)
        self.assertEqual(out['status'],'ok')
        self.assertEqual(out['colonizations'],row['colonizations'])
        self.assertEqual(out['building_costs'],row['building_costs'])
        self.assertEqual(row,original)
        out['building_costs'][0]['cost'][0]['amount']=1
        self.assertEqual(row['building_costs'][0]['cost'][0]['amount'],300)
    def test_three_capture_identities_and_local_uid_must_match(self):
        for key,value in [('session_key','other'),('capture_run_id','other'),
                          ('capture_segment_id','other'),('local_player_entity_uid',55)]:
            row=operations();row[key]=value
            self.assertEqual(OP.project_operations(row,snapshot())['status'],'rejected')
    def test_local_parent_human_identity_required(self):
        parent=snapshot();parent['local_player_identity']['is_ai']=True
        self.assertEqual(OP.project_operations(operations(),parent)['status'],'rejected')
    def test_invalid_source_schema_and_status_fail_closed(self):
        for key,value in [('schema_version',True),('source','secret-path'),('status','read_error')]:
            row=operations();row[key]=value
            out=OP.project_operations(row,snapshot())
            self.assertEqual(out['status'],'rejected')
            self.assertNotIn('secret-path',json.dumps(out))
    def test_source_partial_never_upgraded(self):
        row=operations();row['status']='partial'
        self.assertEqual(OP.project_operations(row,snapshot())['status'],'partial')
    def test_source_diagnostics_are_fixed_whitelist(self):
        row=operations();row['unknown_fields']=['C:/private/account/secret'];row['reason_codes']=[{'pointer':987654321}]
        out=OP.project_operations(row,snapshot())
        text=json.dumps(out)
        self.assertEqual(out['status'],'partial')
        self.assertNotIn('private',text);self.assertNotIn('987654321',text)
        self.assertIn('unrecognized_operation_diagnostic_omitted',out['reason_codes'])
    def test_unknown_feast_values_do_not_become_zero(self):
        row=operations();row['feast']['goat_cost_reduction']={'account':'SECRET'};del row['feast']['free_feast']
        out=OP.project_operations(row,snapshot())
        self.assertIsNone(out['feast']['goat_cost_reduction']);self.assertIsNone(out['feast']['free_feast'])
        self.assertEqual(out['status'],'partial');self.assertNotIn('SECRET',json.dumps(out))
    def test_feast_booleans_reject_numeric_lookalikes(self):
        row=operations();row['feast']['has_trial_faith']=0
        out=OP.project_operations(row,snapshot())
        self.assertIsNone(out['feast']['has_trial_faith']);self.assertEqual(out['status'],'partial')
    def test_hidden_zone_is_omitted(self):
        row=operations();row['colonizations'].append({'zone_id':999999,'paid_cost':[{'resource':'Food','amount':2}]})
        out=OP.project_operations(row,snapshot())
        self.assertEqual(len(out['colonizations']),1)
        self.assertNotIn('999999',json.dumps(out));self.assertEqual(out['status'],'partial')
    def test_foreign_or_mismatched_building_is_omitted(self):
        for changes in ({'uid':777777},{'zone_id':88},{'kind':'SecretEnemyBuilding'}):
            row=operations();row['building_costs'][0].update(changes)
            out=OP.project_operations(row,snapshot())
            self.assertEqual(out['building_costs'],[]);self.assertEqual(out['status'],'partial')
            self.assertNotIn('SecretEnemyBuilding',json.dumps(out))
    def test_cost_extra_fields_unknown_resources_and_invalid_numbers_rejected(self):
        variants=[{'resource':'Food','amount':5,'pointer':987654321},
            {'resource':'AccountID','amount':4},{'resource':'Food','amount':float('nan')},
            {'resource':'Food','amount':float('inf')},{'resource':'Food','amount':True},
            {'resource':'Food','amount':-1},{'resource':'Food','amount':10**1000}]
        for cost in variants:
            row=operations();row['colonizations'][0]['paid_cost']=[cost]
            out=OP.project_operations(row,snapshot())
            self.assertIsNone(out['colonizations'][0]['paid_cost'])
            self.assertEqual(out['colonizations'][0]['paid_cost_record_status'],'unavailable')
    def test_no_record_and_unavailable_remain_different(self):
        row=operations();row['colonizations'][0]['extra_paid_cost_record_status']='unavailable'
        out=OP.project_operations(row,snapshot())
        self.assertIsNone(out['colonizations'][0]['extra_paid_cost'])
        self.assertEqual(out['colonizations'][0]['extra_paid_cost_record_status'],'unavailable')
        self.assertEqual(out['status'],'partial')
    def test_legacy_missing_record_status_stays_unknown(self):
        row=operations();del row['colonizations'][0]['extra_paid_cost_record_status']
        out=OP.project_operations(row,snapshot())
        self.assertEqual(out['colonizations'][0]['extra_paid_cost_record_status'],'unavailable')
    def test_parent_building_scope_must_be_owned(self):
        parent=snapshot();parent['buildings'][0]['zone_id']=88
        self.assertEqual(OP.project_operations(operations(),parent)['status'],'rejected')
    def test_duplicate_rows_do_not_generate_duplicate_cost_evidence(self):
        row=operations();row['building_costs']*=2;row['colonizations']*=2
        out=OP.project_operations(row,snapshot())
        self.assertEqual(len(out['building_costs']),1);self.assertEqual(len(out['colonizations']),1)
        self.assertEqual(out['status'],'partial')
    def test_clock_rollback_or_old_operations_rejected(self):
        for changes in ({'game_time_seconds':100},{'time_after':119}, {'sample_span_game_seconds':.5}):
            row=operations();row.update(changes)
            self.assertEqual(OP.project_operations(row,snapshot())['status'],'rejected')
    def test_extras_at_all_levels_never_copied(self):
        row=operations();row['path']='SECRET';row['feast']['player_name']='SECRET';row['colonizations'][0]['enemy_uid']='SECRET'
        row['building_costs'][0]['address']='SECRET'
        out=OP.project_operations(row,snapshot())
        self.assertNotIn('SECRET',json.dumps(out));self.assertNotIn('path',out)
    def test_verified_quote_recomputed_and_stripped_to_four_keys(self):
        out=OP.project_operations(with_quote(operations()),snapshot())
        self.assertEqual(out['feast_quote'],{'status':'verified','source':OP.QUOTE_SOURCE,
            'cost':[{'resource':'Food','amount':510}],'formula_branch':'paid_feast_zero_modifiers'})
    def test_quote_fingerprint_input_cost_branch_and_mode_must_match(self):
        for mutation in ('hash','input','cost','branch','mode','source'):
            row=with_quote(operations());parent=snapshot();q=row['feast_quote']
            if mutation=='hash':q['build_evidence']['data_cdb_sha256']='wrong'
            elif mutation=='input':q['input_evidence']['feast_count']=5
            elif mutation=='cost':q['cost'][0]['amount']=0
            elif mutation=='branch':q['formula_branch']='free'
            elif mutation=='mode':parent['game_context']['editor_present']=True
            else:q['source']='secret'
            out=OP.project_operations(row,parent)
            self.assertEqual(out['feast_quote']['status'],'unsupported');self.assertEqual(out['feast_quote']['cost'],[])
    def test_unsupported_quote_does_not_become_zero_payment(self):
        row=operations();row['feast_quote']={'status':'unsupported','cost':[{'resource':'Food','amount':0}]}
        self.assertEqual(OP.project_operations(row,snapshot())['feast_quote']['cost'],[])
    def test_partial_source_cannot_retain_verified_quote(self):
        row=with_quote(operations());row['status']='partial'
        self.assertEqual(OP.project_operations(row,snapshot())['feast_quote']['status'],'unsupported')
    def test_quote_bool_as_numeric_input_rejected(self):
        row=with_quote(operations());row['feast_quote']['input_evidence']['free_feast']=False
        self.assertEqual(OP.project_operations(row,snapshot())['feast_quote']['status'],'unsupported')


if __name__=='__main__':unittest.main()
