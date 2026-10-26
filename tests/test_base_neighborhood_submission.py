"""Invented spatial annotations cannot widen a reviewed own-player capture."""
import copy
import gzip
import sys
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT))
import test_submission_pipeline as pipeline
from companion.core.base_rings import BaseRingTracker,derive_base_neighborhood
from companion.core.map_projection import SOURCE,HLBOOT_SHA256,DATA_CDB_SHA256
from prepare_submission import pseudonymize
from submission_contract import RECORD_SCHEMAS,SubmissionError,loads,project,validate
from validate_submission import BaseNeighborhoodValidation,validate_observation_scope,validate_submission


def observation(when=100.,edges=None):
    row=pipeline.invented_observation(when)
    if edges is None:
        edges={1:[2,3],2:[1,4],3:[1,4],4:[2,3,5],5:[4],6:[]}
    row['discovered_zone_ids']=sorted(edges)
    row['buildings'][0]['is_construction']=False
    row['units'][0]['zone_id']=2
    row['labor']['units'][0]['zone_id']=2
    row['labor']['units'][0]['assignment']['same_zone']=False
    row['map_observation']={'schema_version':1,'policy_version':'player-map.v1','status':'ok',
        'source':SOURCE,'scope':'local_player_discovered_static_cells',
        'adjacency_semantics':'geometric_map_cell_neighbors_not_traversability',
        'hlboot_sha256':HLBOOT_SHA256,'data_cdb_sha256':DATA_CDB_SHA256,
        **{key:row[key] for key in ('session_key','capture_run_id','capture_segment_id','local_player_entity_uid')},
        'game_time_seconds':when,'map_identity':{'map_instance_id':row['session_key'],'biome':'Steppe'},
        'known_zones':[{'id':zid,'known_geometric_neighbor_ids':neighbors} for zid,neighbors in edges.items()]}
    return row


def shared(row):
    """Use the real whitelist and recursive pseudonymizer, without a package."""
    result=project(copy.deepcopy(row),RECORD_SCHEMAS['player-observations.ndjson.gz'])
    pseudonymize(result)
    return result


def check(row,state=None):
    validate(row,RECORD_SCHEMAS['player-observations.ndjson.gz'])
    validate_observation_scope(row)
    (state or BaseNeighborhoodValidation()).check(row)


class BaseNeighborhoodSubmissionTests(unittest.TestCase):
    def test_real_tracker_package_roundtrip_and_recursive_whitelist(self):
        harness=pipeline.SubmissionPipelineTests();harness.setUp()
        try:
            tracker=BaseRingTracker();rows=[observation(100.),observation(101.)]
            for row in rows:
                row['base_neighborhood']=tracker.update(row)
            rows[0]['base_neighborhood']['zones'][0]['private_pointer']='DO_NOT_EXPORT_BASE_POINTER'
            harness.write_rows('player-observations.ndjson',rows)
            harness.write_rows('health.ndjson',[{'status':'ok','base_neighborhood_status':'partial',
                'component_diagnostics':{'base_neighborhood':{'source_status':'derived',
                    'projection_status':'partial','projection_reason_codes':['known_graph_only_complete_rings_unverified']}}}])
            harness.write_rows('economic-snapshots.ndjson',[{'status':'ok','game_time_seconds':100.,
                'base_neighborhood':rows[0]['base_neighborhood']}])
            directory,archive=harness.export()
            self.assertEqual(validate_submission(directory)['metadata']['clan'],'Bear')
            validate_submission(archive)
            exported=[loads(line) for line in gzip.decompress((directory/'player-observations.ndjson.gz').read_bytes()).splitlines()]
            base=exported[0]['base_neighborhood']
            self.assertEqual(base['session_key'],exported[0]['session_key'])
            self.assertNotEqual(base['session_key'],'session')
            self.assertEqual(base['ring1_zone_ids'],[2,3]);self.assertEqual(base['ring2_zone_ids'],[4])
            self.assertEqual(base['known_unclassified_zone_ids'],[5,6])
            self.assertNotIn(b'DO_NOT_EXPORT_BASE_POINTER',gzip.decompress((directory/'player-observations.ndjson.gz').read_bytes()))
            self.assertNotIn('base_neighborhood',loads(gzip.decompress((directory/'economic-snapshots.ndjson.gz').read_bytes())))
            harness.rewrite(directory,'player-observations.ndjson.gz',
                            lambda values:values[0]['base_neighborhood']['zones'][1].update(own_unit_uids=[999]))
            with self.assertRaises(SubmissionError):
                validate_submission(directory)
        finally:
            harness.tearDown()

    def test_forged_geometry_entities_provenance_and_completeness_fail(self):
        value=observation();value['base_neighborhood']=BaseRingTracker().update(value)
        source=shared(value);check(source)
        mutations={
            'unknown_zone':lambda b:b['ring1_zone_ids'].append(999),
            'wrong_ring':lambda b:b.update(ring1_zone_ids=[2],ring2_zone_ids=[3,4]),
            'ring_duplicates':lambda b:b['ring1_zone_ids'].append(2),
            'missing_zone':lambda b:b['zones'].pop(),
            'foreign_unit':lambda b:b['zones'][1].update(own_unit_uids=[999]),
            'building_as_unit':lambda b:b['zones'][0].update(own_unit_uids=[100]),
            'foreign_building':lambda b:b['zones'][0].update(own_building_uids=[999]),
            'illegal_witness':lambda b:b['zones'][3].update(witness_path_zone_ids=[1,5,4]),
            'forged_anchor':lambda b:b['anchor'].update(zone_id=2,town_hall_uid=110),
            'forged_anchor_first_time':lambda b:b['anchor'].update(first_observed_game_time_seconds=99.),
            'different_local_player':lambda b:b.update(local_player_entity_uid=99),
            'different_segment':lambda b:b.update(capture_segment_id='f'*24),
            'different_clock':lambda b:b.update(game_time_seconds=101.),
            'different_map_clock':lambda b:b['zones'][0].update(map_observed_game_time_seconds=99.),
            'different_entities_clock':lambda b:b['zones'][0].update(own_entities_observed_game_time_seconds=99.),
            'invented_owner':lambda b:b['zones'][1].update(ownership='self'),
            'visibility_claim':lambda b:b['zones'][1].update(current_visibility='visible'),
            'complete_claim':lambda b:b.update(coverage='complete'),
            'hidden_owner':lambda b:b['zones'][1].update(owner_uid=99),
            'distance_three':lambda b:b['zones'][4].update(observed_graph_distance=3),
            'missing_unknowns':lambda b:b.update(unknown_fields=[]),
            'missing_incomplete_reason':lambda b:b.update(reason_codes=[]),
            'false_unknown_status':lambda b:b.update(status='unknown'),
        }
        for name,mutate in mutations.items():
            with self.subTest(name=name):
                candidate=copy.deepcopy(source);mutate(candidate['base_neighborhood'])
                with self.assertRaises(SubmissionError):check(candidate)

    def test_asymmetric_map_edges_never_establish_ring_witness(self):
        row=observation(edges={1:[2],2:[],3:[]})
        row['base_neighborhood']=derive_base_neighborhood(row)
        value=shared(row);check(value)
        self.assertEqual(value['base_neighborhood']['ring1_zone_ids'],[])
        self.assertEqual(value['base_neighborhood']['known_unclassified_zone_ids'],[2,3])
        value['base_neighborhood']['ring1_zone_ids']=[2]
        value['base_neighborhood']['known_unclassified_zone_ids']=[3]
        value['base_neighborhood']['zones'][1].update(observed_graph_distance=1,witness_path_zone_ids=[1,2])
        with self.assertRaises(SubmissionError):check(value)

    def test_session_anchor_persists_across_hall_loss_and_capture_boundary(self):
        tracker=BaseRingTracker();state=BaseNeighborhoodValidation()
        first=observation();first['base_neighborhood']=tracker.update(first);check(shared(first),state)
        later=observation(101.)
        later['buildings']=[];later['labor']['buildings']=[];later['labor']['units'][0]['assignment']=None
        later['units'][0]['assign_to']=None
        later['population_growth']['town_halls']=[]
        later['capture_segment_id']='run:2'
        for key in ('labor','population_growth','map_observation'):
            later[key]['capture_segment_id']='run:2'
        later['base_neighborhood']=tracker.update(later)
        value=shared(later);check(value,state)
        self.assertIsNone(value['base_neighborhood']['anchor']['town_hall_uid'])
        self.assertEqual(value['base_neighborhood']['ring1_zone_ids'],[2,3])
        # Another valid owned hall cannot silently move the original anchor.
        moved=observation(102.);moved['owned_zone_ids']=[1,2]
        moved['buildings'][0]['zone_id']=2
        moved['population_growth']['town_halls'][0]['zone_id']=2
        moved['labor']['buildings'][0]['zone_id']=2
        moved['labor']['units'][0]['assignment']['zone_id']=2
        moved['base_neighborhood']=derive_base_neighborhood(moved)
        with self.assertRaises(SubmissionError):check(shared(moved),state)

    def test_missing_current_map_never_carries_historical_graph(self):
        tracker=BaseRingTracker();state=BaseNeighborhoodValidation()
        first=observation();first['base_neighborhood']=tracker.update(first);check(shared(first),state)
        later=observation(101.);del later['map_observation']
        later['base_neighborhood']=tracker.update(later);value=shared(later);check(value,state)
        self.assertEqual(value['base_neighborhood']['status'],'unknown')
        self.assertEqual(value['base_neighborhood']['zones'],[])
        self.assertIsNone(value['base_neighborhood']['anchor']['zone_id'])
        value['base_neighborhood']['ring1_zone_ids']=[2,3]
        with self.assertRaises(SubmissionError):check(value,state)

    def test_legacy_without_component_and_explicit_unavailable_remain_valid(self):
        check(shared(observation()))
        row=observation();row['quality']['identity_verified']=False
        row['base_neighborhood']=BaseRingTracker().update(row)
        check(shared(row))
        row['base_neighborhood']['ring1_zone_ids']=[2]
        with self.assertRaises(SubmissionError):check(shared(row))

    def test_missing_map_keys_fail_with_submission_error(self):
        for field in ('id','known_geometric_neighbor_ids'):
            with self.subTest(field=field):
                row=observation();del row['map_observation']['known_zones'][0][field]
                row['base_neighborhood']=derive_base_neighborhood(row)
                with self.assertRaises(SubmissionError):check(shared(row))

    def test_duplicate_map_rows_are_omitted_from_current_graph(self):
        row=observation()
        row['map_observation']['known_zones'].append(copy.deepcopy(row['map_observation']['known_zones'][1]))
        row['base_neighborhood']=derive_base_neighborhood(row)
        value=shared(row);check(value)
        self.assertEqual(value['base_neighborhood']['ring1_zone_ids'],[3])
        self.assertIn('duplicate_map_zone_omitted',value['base_neighborhood']['reason_codes'])
        self.assertNotIn(2,[zone['zone_id'] for zone in value['base_neighborhood']['zones']])
        value['base_neighborhood']['ring1_zone_ids'].append(2)
        with self.assertRaises(SubmissionError):check(value)

    def test_clock_rollback_does_not_reanchor_before_first_observation(self):
        tracker=BaseRingTracker();state=BaseNeighborhoodValidation()
        first=observation();first['base_neighborhood']=tracker.update(first);check(shared(first),state)
        rollback=observation(95.);rollback['capture_segment_id']='run:2'
        for key in ('labor','population_growth','map_observation'):
            rollback[key]['capture_segment_id']='run:2'
        rollback['base_neighborhood']=tracker.update(rollback)
        value=shared(rollback);check(value,state)
        self.assertIsNone(value['base_neighborhood']['anchor'])
        self.assertEqual(value['base_neighborhood']['ring1_zone_ids'],[])
        forged=copy.deepcopy(value)
        forged['base_neighborhood']=shared({**rollback,'base_neighborhood':derive_base_neighborhood(rollback)})['base_neighborhood']
        with self.assertRaises(SubmissionError):check(forged,state)
        later=observation(101.);later['base_neighborhood']=tracker.update(later);check(shared(later),state)
        self.assertEqual(later['base_neighborhood']['anchor']['first_observed_game_time_seconds'],100.)

    def test_new_session_uses_its_own_first_observed_anchor(self):
        tracker=BaseRingTracker();state=BaseNeighborhoodValidation()
        first=observation();first['base_neighborhood']=tracker.update(first);check(shared(first),state)
        second=observation(101.);second['session_key']='different-session'
        second['owned_zone_ids']=[1,2];second['buildings'][0]['zone_id']=2
        second['population_growth']['town_halls'][0]['zone_id']=2
        second['labor']['buildings'][0]['zone_id']=2
        second['labor']['units'][0]['assignment']['zone_id']=2
        for key in ('labor','population_growth','map_observation'):
            second[key]['session_key']='different-session'
        second['map_observation']['map_identity']['map_instance_id']='different-session'
        second['base_neighborhood']=tracker.update(second);check(shared(second),state)
        self.assertEqual(second['base_neighborhood']['ring0_zone_ids'],[2])
        self.assertEqual(second['base_neighborhood']['anchor']['first_observed_game_time_seconds'],101.)

    def test_unavailable_or_ambiguous_completed_hall_has_no_guessed_anchor(self):
        for ambiguous in (False,True):
            with self.subTest(ambiguous=ambiguous):
                row=observation()
                if ambiguous:
                    row['buildings'].append({**row['buildings'][0],'uid':101})
                else:
                    del row['buildings'][0]['is_construction']
                row['base_neighborhood']=derive_base_neighborhood(row)
                value=shared(row);check(value)
                self.assertIsNone(value['base_neighborhood']['anchor'])
                self.assertEqual(value['base_neighborhood']['known_unclassified_zone_ids'],[1,2,3,4,5,6])
                self.assertEqual(value['base_neighborhood']['ring0_zone_ids'],[])

    def test_legacy_frames_do_not_claim_a_preexisting_tracker_anchor(self):
        state=BaseNeighborhoodValidation();check(shared(observation()),state)
        later=observation(101.);later['owned_zone_ids']=[1,2]
        later['buildings'][0]['zone_id']=2;later['population_growth']['town_halls'][0]['zone_id']=2
        later['labor']['buildings'][0]['zone_id']=2;later['labor']['units'][0]['assignment']['zone_id']=2
        later['base_neighborhood']=BaseRingTracker().update(later);check(shared(later),state)
        self.assertEqual(later['base_neighborhood']['anchor']['first_observed_game_time_seconds'],101.)


if __name__=='__main__':
    unittest.main()
