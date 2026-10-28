"""Invented map details test scope, quantities and optional failure isolation."""
import copy
import math
import unittest

from companion.core.known_zone_details import (
    DATA_CDB_SHA256, HLBOOT_SHA256, SOURCE, SCOPE, UNKNOWNS,
    RESOURCE_REFERENCES, project_known_zone_details, validate_known_zone_details)
from companion.core.player_observation import project_player_observation


def fixture(when=100.):
    parent={'schema_version':2,'status':'ok','session_key':'session','capture_run_id':'run',
        'capture_segment_id':'run:1','local_player_entity_uid':31,'game_time_seconds':when,
        'local_player_identity':{'verified':True,'role':'player','entity_uid':31,
            'is_ai':False,'in_player_list':True},'clan':'Bear',
        'owned_zone_ids':[1],'discovered_zone_ids':[1,2],
        'game_context':{'mode':{'kind':'Singleplayer','args':[{'kind':'FreeForAll','args':[]}]},
            'game_type':{'kind':'Standard','args':[]},
            'editor_present':False,'bifrost_data_present':False},
        'stocks':{k:100. for k in ('Food','Wood','Money','Stone','Iron','Lore')},
        'units':[],'buildings':[],'population':4,'population_cap':6,'in_winter':False}
    def zone(zid):
        return {'zone_id':zid,'resources_status':'ok','wildlife_status':'ok',
            'natural_attributes_status':'complete','tall_grass_present':False,'hiding_grass_present':False,
            'resources':[],'wildlife':[{'kind':'Wolf','count':0},{'kind':'Bear','count':0}],
            'resource_reason_codes':[],'wildlife_reason_codes':[]}
    value={'schema_version':1,'status':'partial','source':SOURCE,'scope':SCOPE,
        'hlboot_sha256':HLBOOT_SHA256,'data_cdb_sha256':DATA_CDB_SHA256,
        **{k:parent[k] for k in ('session_key','capture_run_id','capture_segment_id','local_player_entity_uid')},
        'game_time_seconds':when,'time_before':when,'time_after':when,
        'sample_span_game_seconds':0.,'atomic':False,'zones':[zone(1),zone(2)],
        'reason_codes':[],'unknown_fields':list(UNKNOWNS)}
    value['zones'][1]['resources']=[{'kind':'Stones','object_count':2,
        'deposits':[{'natural_object_uid':1001,'remaining_contains_raw':12.,'next_extraction_progress_raw':.3},
                    {'natural_object_uid':1002,'remaining_contains_raw':8.5,'next_extraction_progress_raw':.8}],
        'remaining_total_raw':20.5,'modeled_ui_remaining_total':21},
        {'kind':'RuneStone','object_count':1,'deposits':[],
         'remaining_total_raw':None,'modeled_ui_remaining_total':None}]
    value['zones'][1]['wildlife']=[{'kind':'Wolf','count':2},{'kind':'Bear','count':0}]
    return value,parent


class KnownZoneProjectionTests(unittest.TestCase):
    def test_explicit_native_null_game_type_is_not_missing_or_standard(self):
        value, parent = fixture()
        parent['game_context']['game_type'] = None
        parent['known_zone_details'] = value
        out = project_player_observation(parent)
        self.assertEqual(out['known_zone_details']['status'], 'partial')
        self.assertEqual(out['game_context']['game_type_state'], 'native_null')
        self.assertNotIn('game_type_kind', out['game_context'])
        validate_known_zone_details(out['known_zone_details'], out)

    def test_missing_game_type_does_not_become_a_native_null_proof(self):
        value, parent = fixture()
        parent['game_context'].pop('game_type')
        parent['known_zone_details'] = value
        out = project_player_observation(parent)
        self.assertEqual(out['known_zone_details']['status'], 'rejected')
        self.assertNotIn('game_type_state', out['game_context'])

    def test_standard_enum_context_retains_its_distinct_state(self):
        value, parent = fixture()
        parent['known_zone_details'] = value
        out = project_player_observation(parent)
        self.assertEqual(out['game_context']['game_type_state'], 'enum')
        self.assertEqual(out['game_context']['game_type_kind'], 'Standard')

    def test_projected_null_state_cannot_contradict_a_named_game_type(self):
        value, parent = fixture()
        parent['known_zone_details'] = value
        out = project_player_observation(parent)
        out['game_context']['game_type_state'] = 'native_null'
        with self.assertRaises(ValueError):
            validate_known_zone_details(out['known_zone_details'], out)

    def test_known_uncolonized_resource_and_creature_subset_is_preserved(self):
        value,parent=fixture();before=copy.deepcopy(value)
        out=project_known_zone_details(value,parent)
        self.assertEqual(out['status'],'partial');self.assertEqual(value,before)
        resource=out['zones'][1]['resources'][0]
        self.assertEqual(resource['remaining_total_raw'],20.5)
        self.assertEqual(resource['modeled_ui_remaining_total'],21)
        self.assertEqual(resource['reference_properties'],RESOURCE_REFERENCES['Stones'])
        self.assertEqual(out['zones'][1]['wildlife'][0]['count'],2)
        self.assertNotIn('owner',str(out))

    def test_reference_jobs_and_multipliers_are_configuration_not_yields(self):
        self.assertEqual(RESOURCE_REFERENCES['RuneStone']['fixed_assignment_slots'],1)
        self.assertEqual(RESOURCE_REFERENCES['CircleStone']['fixed_assignment_slots'],2)
        self.assertEqual(RESOURCE_REFERENCES['Grove']['local_multiplier_reference'][0]['multiplier'],1.1)
        self.assertEqual(RESOURCE_REFERENCES['DeerSpawner']['depletion_model'],'renewing_deer_source')
        self.assertEqual(RESOURCE_REFERENCES['Stones']['semantics'],'verified_default_configuration_not_effective_yield')

    def test_optional_bad_component_keeps_core_economy_and_identity(self):
        value,parent=fixture();value['zones'][1]['owner_name']='PRIVATE'
        parent['known_zone_details']=value
        out=project_player_observation(parent)
        self.assertEqual(out['status'],'ok');self.assertEqual(out['stocks']['Wood'],100.)
        self.assertEqual(out['known_zone_details']['status'],'rejected')
        self.assertNotIn('PRIVATE',str(out))

    def test_old_capture_does_not_gain_a_component_or_reconstructed_data(self):
        _,parent=fixture()
        self.assertNotIn('known_zone_details',project_player_observation(parent))

    def test_unsupported_and_read_error_are_data_free(self):
        for status in ('unsupported_context','unavailable'):
            value,parent=fixture();value.update(status=status,private_pointer='PRIVATE',zones=[{'secret':'PRIVATE'}])
            out=project_known_zone_details(value,parent)
            self.assertEqual(out['status'],status);self.assertEqual(out['zones'],[])
            self.assertEqual(set(out),{'schema_version','status','source','scope','zones','reason_codes','unknown_fields'})
            self.assertNotIn('PRIVATE',str(out));validate_known_zone_details(out,parent)

    def test_context_identity_fingerprints_clocks_and_hidden_references_rejected(self):
        mutations=[
            lambda v,p:v.update(hlboot_sha256='0'*64),lambda v,p:v.update(data_cdb_sha256='0'*64),
            lambda v,p:v.update(schema_version=True),lambda v,p:v.update(source='unverified'),
            lambda v,p:v.update(scope='all_world'),lambda v,p:v.update(atomic=True),
            lambda v,p:v.update(capture_segment_id='other'),lambda v,p:v.update(local_player_entity_uid=99),
            lambda v,p:v.update(game_time_seconds=200.),lambda v,p:v.update(time_before=101.),
            lambda v,p:v.update(sample_span_game_seconds=1.),lambda v,p:v.update(unknown_fields=[]),
            lambda v,p:p['game_context'].update(editor_present=True),
            lambda v,p:p['game_context'].update(mode={'kind':'Multiplayer','args':[{'kind':'Team3V3','args':[]}]}),
            lambda v,p:p['local_player_identity'].update(is_ai=True),
            lambda v,p:v['zones'][1].update(zone_id=999),lambda v,p:v['zones'].pop(),
            lambda v,p:v['zones'].append(copy.deepcopy(v['zones'][0])),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                value,parent=fixture();mutate(value,parent)
                self.assertEqual(project_known_zone_details(value,parent)['status'],'rejected')

    def test_mineral_nulls_are_not_exhaustion_or_ui_zero(self):
        value,parent=fixture();zone=value['zones'][1];zone['resources_status']='partial'
        resource=zone['resources'][0];resource['deposits'][0]['remaining_contains_raw']=None
        resource['remaining_total_raw']=resource['modeled_ui_remaining_total']=None
        out=project_known_zone_details(value,parent)
        self.assertEqual(out['status'],'partial')
        self.assertIsNone(out['zones'][1]['resources'][0]['remaining_total_raw'])
        self.assertIsNone(out['zones'][1]['resources'][0]['deposits'][0]['remaining_contains_raw'])

    def test_finite_stock_progress_count_and_aggregate_forgery_rejected(self):
        mutations=[
            lambda r:r.update(object_count=1),lambda r:r.update(object_count=True),
            lambda r:r.update(remaining_total_raw=99),lambda r:r.update(modeled_ui_remaining_total=20),
            lambda r:r['deposits'][0].update(remaining_contains_raw=-1),
            lambda r:r['deposits'][0].update(next_extraction_progress_raw=math.nan),
            lambda r:r['deposits'][0].update(next_extraction_progress_raw=True),
            lambda r:r['deposits'][0].update(remaining_contains_raw=None),
            lambda r:r.update(kind='GoldDeposit'),lambda r:r.update(owner_uid=99),
            lambda r:r['deposits'][1].update(natural_object_uid=1001),
            lambda r:r['deposits'][1].update(natural_object_uid=0),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                value,parent=fixture();mutate(value['zones'][1]['resources'][0])
                self.assertEqual(project_known_zone_details(value,parent)['status'],'rejected')

    def test_non_mineral_cannot_claim_remaining_knowledge(self):
        value,parent=fixture();value['zones'][1]['resources'][1]['remaining_total_raw']=500
        self.assertEqual(project_known_zone_details(value,parent)['status'],'rejected')

    def test_partial_and_grass_coverage_cannot_claim_zero_wildlife(self):
        for mutate in (
            lambda z:z.update(wildlife_status='partial'),
            lambda z:z.update(tall_grass_present=True),
            lambda z:z.update(natural_attributes_status='unavailable',tall_grass_present=None,hiding_grass_present=None),
        ):
            value,parent=fixture();mutate(value['zones'][1])
            self.assertEqual(project_known_zone_details(value,parent)['status'],'rejected')
        value,parent=fixture();zone=value['zones'][1]
        zone.update(wildlife_status='partial',tall_grass_present=True,wildlife=[{'kind':'Wolf','count':2}])
        self.assertEqual(project_known_zone_details(value,parent)['status'],'partial')

    def test_unavailable_zone_payload_and_duplicate_kinds_rejected(self):
        for mutate in (
            lambda z:z.update(resources_status='unavailable'),
            lambda z:z.update(wildlife_status='unavailable'),
            lambda z:z['resources'].append(copy.deepcopy(z['resources'][0])),
            lambda z:z['wildlife'].append(copy.deepcopy(z['wildlife'][0])),
            lambda z:z['wildlife'][0].update(kind='BearMaiden'),
        ):
            value,parent=fixture();mutate(value['zones'][1])
            self.assertEqual(project_known_zone_details(value,parent)['status'],'rejected')

    def test_unknown_diagnostics_cannot_export_strings(self):
        value,parent=fixture();value['reason_codes']=['PRIVATE_NAME']
        self.assertEqual(project_known_zone_details(value,parent)['status'],'rejected')

    def test_mutated_reference_properties_cannot_pass_projected_validation(self):
        value,parent=fixture();out=project_known_zone_details(value,parent)
        out['zones'][1]['resources'][1]['reference_properties']['fixed_assignment_slots']=True
        with self.assertRaises(ValueError):validate_known_zone_details(out,parent)

    def test_same_natural_uid_can_link_to_owned_mineral_but_not_worker_or_wrong_kind(self):
        value,parent=fixture()
        parent['owned_zone_ids'].append(2)
        parent['buildings']=[{'uid':1001,'kind':'Stones','zone_id':2}]
        self.assertEqual(project_known_zone_details(value,parent)['status'],'partial')
        parent['buildings'][0]['kind']='Mine'
        self.assertEqual(project_known_zone_details(value,parent)['status'],'rejected')
        parent['buildings']=[];parent['units']=[{'uid':1001,'kind':'Villager','zone_id':2}]
        self.assertEqual(project_known_zone_details(value,parent)['status'],'rejected')


if __name__=='__main__':unittest.main()
