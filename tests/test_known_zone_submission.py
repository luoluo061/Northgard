"""Reviewed-package tests for the limited discovered-natural component."""
import copy
import gzip
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT))
import test_submission_pipeline as pipeline
from companion.tests.test_known_zone_details import fixture
from companion.core.known_zone_details import (
    HLBOOT_SHA256,DATA_CDB_SHA256,project_known_zone_details)
from prepare_submission import pseudonymize
from submission_contract import RECORD_SCHEMAS,SubmissionError,loads,project,validate
from validate_submission import validate_observation_scope,validate_submission


def observation(when=100.):
    row=pipeline.invented_observation(when)
    row['game_context']['game_type_kind']='Standard'
    raw,parent=fixture(when)
    parent.update({key:row[key] for key in ('session_key','capture_run_id','capture_segment_id','local_player_entity_uid')})
    raw.update({key:row[key] for key in ('session_key','capture_run_id','capture_segment_id','local_player_entity_uid')})
    row['known_zone_details']=project_known_zone_details(raw,parent)
    return row


def shared(row):
    result=project(copy.deepcopy(row),RECORD_SCHEMAS['player-observations.ndjson.gz'])
    pseudonymize(result)
    return result


def check(row):
    validate(row,RECORD_SCHEMAS['player-observations.ndjson.gz'])
    validate_observation_scope(row)


class KnownZoneSubmissionTests(unittest.TestCase):
    def test_native_null_context_roundtrip_and_missing_or_conflicting_proofs(self):
        row=observation()
        row['game_context'].pop('game_type_kind')
        row['game_context']['game_type_state']='native_null'
        source=shared(row);check(source)
        missing=copy.deepcopy(source);missing['game_context'].pop('game_type_state')
        with self.assertRaises(SubmissionError):check(missing)
        conflict=copy.deepcopy(source);conflict['game_context']['game_type_kind']='Standard'
        with self.assertRaises(SubmissionError):check(conflict)
        multiplayer=copy.deepcopy(source);multiplayer['game_context'].update(
            mode_kind='Multiplayer',mode_variant_kind='Team3V3')
        with self.assertRaises(SubmissionError):check(multiplayer)
        harness=self.harness()
        rows=[observation(100.),observation(101.)]
        for value in rows:
            value['game_context'].pop('game_type_kind')
            value['game_context']['game_type_state']='native_null'
        harness.write_rows('player-observations.ndjson',rows)
        directory,archive=harness.export()
        validate_submission(directory);validate_submission(archive)
        exported=[loads(line) for line in gzip.decompress(
            (directory/'player-observations.ndjson.gz').read_bytes()).splitlines()]
        self.assertTrue(all(value['game_context']['game_type_state']=='native_null' for value in exported))
        self.assertTrue(all('game_type_kind' not in value['game_context'] for value in exported))

    def harness(self):
        harness=pipeline.SubmissionPipelineTests();harness.setUp()
        self.addCleanup(harness.tearDown)
        harness.manifest['compatibility']={'hlboot_sha256':HLBOOT_SHA256,'data_cdb_sha256':DATA_CDB_SHA256}
        harness.write_manifest()
        harness.write_rows('player-observations.ndjson',[observation(100.),observation(101.)])
        return harness

    def test_real_package_roundtrip_whitelists_and_pseudonymizes(self):
        harness=self.harness();rows=[observation(100.),observation(101.)]
        rows[0]['known_zone_details']['zones'][1]['private_pointer']='DO_NOT_EXPORT_POINTER'
        rows[0]['known_zone_details']['zones'][1]['resources'][0]['private_account']='DO_NOT_EXPORT_ACCOUNT'
        harness.write_rows('player-observations.ndjson',rows)
        harness.write_rows('economic-snapshots.ndjson',[{'status':'ok','game_time_seconds':100.,
            'known_zone_details':rows[0]['known_zone_details']}])
        harness.write_rows('health.ndjson',[{'status':'ok','known_zone_details_status':'partial',
            'known_zone_details_unknown_fields':['unsupported_resource_kinds'],
            'component_diagnostics':{'known_zone_details':{'source_status':'partial','projection_status':'partial'}}}])
        directory,archive=harness.export()
        validate_submission(directory);validate_submission(archive)
        raw=gzip.decompress((directory/'player-observations.ndjson.gz').read_bytes())
        self.assertNotIn(b'DO_NOT_EXPORT',raw)
        exported=[loads(line) for line in raw.splitlines()]
        component=exported[0]['known_zone_details']
        self.assertEqual(component['capture_run_id'],exported[0]['capture_run_id'])
        self.assertNotEqual(component['capture_run_id'],'run')
        self.assertEqual(component['zones'][1]['resources'][0]['remaining_total_raw'],20.5)
        self.assertNotIn('known_zone_details',loads(gzip.decompress((directory/'economic-snapshots.ndjson.gz').read_bytes())))

    def test_independent_validation_rejects_forged_scope_and_quantities(self):
        source=shared(observation());check(source)
        mutations={
            'unknown_zone':lambda c:c['zones'][1].update(zone_id=999),
            'missing_zone':lambda c:c['zones'].pop(),
            'duplicate_zone':lambda c:c['zones'][1].update(zone_id=1),
            'missing_identity':lambda c:c.pop('capture_run_id'),
            'wrong_identity':lambda c:c.update(session_key='f'*24),
            'wrong_local_player':lambda c:c.update(local_player_entity_uid=999),
            'wrong_build':lambda c:c.update(data_cdb_sha256='f'*64),
            'wrong_clock':lambda c:c.update(game_time_seconds=500.),
            'wrong_span':lambda c:c.update(sample_span_game_seconds=2.),
            'missing_limits':lambda c:c.update(unknown_fields=[]),
            'whole_world_scope':lambda c:c.update(scope='whole_world'),
            'new_arbitrary_key':lambda c:c.update(private_pointer='PRIVATE'),
            'non_miners_stock':lambda c:c['zones'][1]['resources'][1].update(remaining_total_raw=500),
            'wrong_mineral_total':lambda c:c['zones'][1]['resources'][0].update(remaining_total_raw=30),
            'wrong_ui_total':lambda c:c['zones'][1]['resources'][0].update(modeled_ui_remaining_total=20),
            'wrong_object_count':lambda c:c['zones'][1]['resources'][0].update(object_count=1),
            'duplicate_deposit_uid':lambda c:c['zones'][1]['resources'][0]['deposits'][1].update(natural_object_uid=1001),
            'worker_as_deposit':lambda c:c['zones'][1]['resources'][0]['deposits'][0].update(natural_object_uid=110),
            'wrong_own_building':lambda c:c['zones'][1]['resources'][0]['deposits'][0].update(natural_object_uid=100),
            'null_not_zero':lambda c:c['zones'][1]['resources'][0]['deposits'][0].update(remaining_contains_raw=None),
            'effective_rate_claim':lambda c:c['zones'][1]['resources'][0].update(native_actual_rate=3),
            'wrong_reference_slots':lambda c:c['zones'][1]['resources'][1]['reference_properties'].update(fixed_assignment_slots=2),
            'reference_bool_as_int':lambda c:c['zones'][1]['resources'][1]['reference_properties'].update(fixed_assignment_slots=True),
            'unknown_enemy':lambda c:c['zones'][1]['wildlife'][0].update(kind='Warrior'),
            'own_bear_as_wild':lambda c:c['zones'][1]['wildlife'][0].update(kind='BearMaiden'),
            'duplicate_creature':lambda c:c['zones'][1]['wildlife'].append({'kind':'Wolf','count':3}),
            'partial_zero':lambda c:c['zones'][1].update(wildlife_status='partial'),
            'grass_zero':lambda c:c['zones'][1].update(tall_grass_present=True),
            'unknown_grass':lambda c:c['zones'][1].update(natural_attributes_status='unavailable'),
            'unavailable_data':lambda c:c['zones'][1].update(resources_status='unavailable'),
        }
        for label,mutate in mutations.items():
            with self.subTest(label=label):
                candidate=copy.deepcopy(source);mutate(candidate['known_zone_details'])
                with self.assertRaises(SubmissionError):check(candidate)

    def test_multiplayer_cannot_relabel_a_singleplayer_component(self):
        row=shared(observation());row['game_context'].update(mode_kind='Multiplayer',mode_variant_kind='Team3V3')
        with self.assertRaises(SubmissionError):check(row)

    def test_nonstandard_or_missing_context_cannot_bypass_scope(self):
        for change in ('missing','Campaign'):
            row=shared(observation())
            if change=='missing':row['game_context'].pop('game_type_kind')
            else:row['game_context']['game_type_kind']=change
            with self.assertRaises(SubmissionError):check(row)

    def test_unavailable_diagnostic_accepts_no_payload(self):
        raw,parent=fixture();raw.update(status='unavailable')
        row=observation();row['known_zone_details']=project_known_zone_details(raw,parent)
        source=shared(row);check(source)
        for key,value in [('zones',[{'zone_id':2}]),('local_player_entity_uid',31),('game_time_seconds',100.)]:
            candidate=copy.deepcopy(source);candidate['known_zone_details'][key]=value
            with self.assertRaises(SubmissionError):check(candidate)

    def test_package_fingerprint_binds_known_zone_component(self):
        harness=self.harness();directory,_=harness.export()
        harness.rewrite(directory,'player-observations.ndjson.gz',lambda rows:rows[0]['known_zone_details'].update(
            hlboot_sha256='f'*64))
        with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_resigned_package_does_not_bypass_component_semantics(self):
        harness=self.harness();directory,_=harness.export()
        harness.rewrite(directory,'player-observations.ndjson.gz',lambda rows:rows[0]['known_zone_details']['zones'][1]
            ['resources'][0].update(remaining_total_raw=25.))
        with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_legacy_rows_still_have_no_added_map_details(self):
        row=pipeline.invented_observation();self.assertNotIn('known_zone_details',shared(row))
        check(shared(row))


if __name__=='__main__':unittest.main()
