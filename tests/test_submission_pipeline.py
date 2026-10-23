"""Behavioral tests use invented own-player fixtures, never a real participant."""
import copy
import ast
import contextlib
import itertools
import gzip
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT))
from prepare_submission import prepare
from validate_submission import validate_submission,validate_tree
from build_catalog import build_catalog
from submission_contract import (FILES,RECORD_SCHEMAS,SubmissionError,clock,digest,json_bytes,loads,validate)
from companion.core.player_observation import project_player_observation
from companion.core.growth_projection import project_growth
from companion.core.labor_projection import project_labor
from companion.tests.test_growth_projection import fixture as growth_fixture
from companion.core.action_diff import ActionDiffer
from companion.core.labor_transitions import LaborTransitionTracker
from companion.core.growth_projection import GrowthProgressTracker


def invented_observation(when=100):
    raw_growth,parent=growth_fixture(float(when),33.+(when-100))
    parent.update(schema_version=2,discovered_zone_ids=[1,2],in_winter=False,
        stocks={key:100. for key in ('Food','Wood','Money','Stone','Iron','Lore')},population_cap=16,
        units=[{'uid':110,'kind':'Farmer','zone_id':1,'assigned_job':None,'cur_job':{'kind':'Active','args':[]},
            'assign_to':{'uid':100},'produce':'Wheat','enabled':True,'has_no_work':False,'position':{'x':1,'y':1}}],
        native_events=[],population_growth=raw_growth)
    raw_labor={'schema_version':1,'status':'ok','source':'native_own_labor_inputs_read_only',
        'hlboot_sha256':raw_growth['hlboot_sha256'],'data_cdb_sha256':raw_growth['data_cdb_sha256'],
        **{key:parent[key] for key in ('session_key','capture_run_id','capture_segment_id','local_player_entity_uid')},
        'game_time_seconds':float(when),'time_before':float(when),'time_after':float(when),'sample_span_game_seconds':0.,
        'reason_codes':[],'unknown_fields':[],
        'player_context':{'clan':'Bear','researched_tech_entries':[{'id':'Lumber','value':True}],
            'upgraded_unit_entries':[{'id':'Farmer','value':True}],'effects':[]},
        'buildings':[{'uid':100,'kind':'TownHall','zone_id':1,'effects':[]}],
        'zones':[{'zone_id':1,'effects':[],'flags':0}],
        'units':[{'uid':110,'kind':'Farmer','zone_id':1,'inputs':{'enabled':True,'has_no_work':False,'produce':'Wheat'},
            'effects':[],'assignment':{'building_uid':100,'zone_id':1},
            'configuration_candidates':[{'resource':'Wheat','configured_base_q':.6,'converts_to':'Food',
                'building_kinds':['Fields'],'additional_requirements_present':True,'requirement_status':'not_evaluated',
                'rate_semantics':'static_configuration_candidate_not_effective_rate'}]}]}
    parent['labor']=raw_labor
    observation=project_player_observation(parent,include_own_events=True)
    # Allow tests to run before the root collector integration lands as well.
    observation['labor']=project_labor(raw_labor,parent)
    observation['population_growth']=project_growth(raw_growth,parent)
    observation['population_growth']['observed_progress_intervals']=[]
    return observation


class SubmissionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.capture=self.root/'capture';self.capture.mkdir()
        self.metadata=self.root/'metadata.json'
        meta=loads((ROOT/'schemas/metadata.example.json').read_bytes())
        meta.update(consent_to_share=True,goal='Test a fifth farmer before winter',clan='Bear',experience='beginner',
            feedback=[{'game_time_seconds':100.,'observation':'Fifth farmer observed',
                'time_precision':'sampled_interval',
                'hypothesis':'Possible labor effect','suggestion':'Compare with fixed happiness',
                'counterexample':'Feast may change rates','confidence':'suspected','unit_uids':[110]}])
        self.metadata.write_bytes(json_bytes(meta))
        self.manifest={'recording_state':'stopped','collector_version':'0.3.0-alpha','collection_id':'invented-collection',
            'game_build':'25096976','compatibility':{'hlboot_sha256':'a'*64,'data_cdb_sha256':'b'*64},
            'private_game_path':'C:\\fake-private\\Northgard','raw_private_research_enabled':True}
        self.write_manifest()
        one=invented_observation(100.);two=invented_observation(101.)
        # All these fields must be discarded even inside a supposedly shared file.
        one['private_name']='DO_NOT_EXPORT_NAME'
        one['units'][0]['private_pointer']='DO_NOT_EXPORT_POINTER'
        one['labor']['player_context']['private_account']='DO_NOT_EXPORT_ACCOUNT'
        one['population_growth']['town_halls'][0]['private_path']='DO_NOT_EXPORT_PATH'
        differ=ActionDiffer()
        baseline=differ.feed(one);action=differ.feed(two)
        action['events'].append({'kind':'unit_assignment_changed','source':'state_diff',
            'entity':{'uid':110,'kind':'Farmer','zone_id':1},
            'confidence':{'level':'high','applies_to':'observed only'},
            'changes':{'assign_to':{'before':None,'after':{'uid':100,'kind':'TownHall','zone_id':1,'entity_scope':'owned_zone_building'}}}})
        for action_row in (baseline,action):
            action_row.update(session_key='session',capture_run_id='run',capture_segment_id='run:1')
        self.write_rows('player-observations.ndjson',[one,two])
        self.write_rows('economic-snapshots.ndjson',[{'status':'ok','session_key':'session','game_time_seconds':100.,
            'stocks':one['stocks'],'population_growth':one['population_growth'],'labor':one['labor']}])
        self.write_rows('actions.ndjson',[baseline,action,{'status':'collector_finished','labor_transitions':[]}])
        self.write_rows('health.ndjson',[{'status':'ok','sequence':1,'game_time_seconds':100.,'labor_status':'partial',
            'individual_rate_status':'computed_getter_no_persistent_unit_cache','population_growth_status':'partial',
            'labor_unknown_fields':['optional_native_input'],'growth_unknown_fields':['growth_bonus']},
            {'status':'collector_read_error','error_type':'PermissionError','reason_code':'process_read_denied'}])
        (self.capture/'research-raw.ndjson').write_text('DO_NOT_EXPORT_RAW')
        (self.capture/'latest.json').write_text('{"account":"DO_NOT_EXPORT_LATEST"}')

    def tearDown(self):
        self.tmp.cleanup()

    def write_manifest(self):
        (self.capture/'manifest.json').write_bytes(json_bytes(self.manifest))

    def write_rows(self,name,rows):
        (self.capture/name).write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8')

    def export(self):
        result=prepare(self.capture,self.metadata,self.root/'review')
        return Path(result['review_directory']),Path(result['review_zip'])

    def resign(self,directory):
        submission=loads((directory/'submission.json').read_bytes())
        for entry in submission['files']:
            raw=(directory/entry['path']).read_bytes()
            entry.update(size_bytes=len(raw),sha256=digest(raw))
            expanded=gzip.decompress(raw) if entry['path'].endswith('.gz') else raw
            entry['expanded_bytes']=len(expanded)
            entry['record_count']=len(expanded.splitlines()) if entry['path'].endswith('.gz') else 1
        (directory/'submission.json').write_bytes(json_bytes(submission))
        (directory/'checksums.sha256').write_text(''.join(digest((directory/name).read_bytes())+'  '+name+'\n'
            for name in FILES if name!='checksums.sha256'),encoding='utf-8')

    def rewrite(self,directory,name,change):
        path=directory/name
        rows=[loads(x) for x in gzip.decompress(path.read_bytes()).splitlines()]
        change(rows)
        path.write_bytes(gzip.compress(b''.join(json.dumps(x).encode()+b'\n' for x in rows),mtime=0))
        self.resign(directory)

    def test_safe_export_roundtrip_and_catalog(self):
        directory,archive=self.export()
        value=validate_submission(directory)
        self.assertEqual(value,validate_submission(archive))
        self.assertEqual(len(validate_tree(self.root/'review/data/submissions')),1)
        self.assertEqual(build_catalog(self.root/'review/data/submissions')['matches'][0]['clan'],'Bear')
        self.assertEqual(set(p.name for p in directory.iterdir()),set(FILES))
        all_raw=b''.join(gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_bytes() for p in directory.iterdir())
        self.assertNotIn(b'DO_NOT_EXPORT',all_raw)
        self.assertNotIn(b'fake-private',all_raw)
        self.assertNotIn(b'"session"',all_raw)
        rows=[loads(x) for x in gzip.decompress((directory/'player-observations.ndjson.gz').read_bytes()).splitlines()]
        self.assertEqual(rows[0]['labor']['player_context']['researched_tech_entries'][0]['id'],'Lumber')
        self.assertIsNone(rows[0]['labor']['units'][0]['native_actual_rate'])
        self.assertEqual(rows[0]['population_growth']['model']['next_recruit_threshold'],105)
        self.assertEqual(rows[0]['game_clock'],clock(100.))
        actions=[loads(x) for x in gzip.decompress((directory/'actions.ndjson.gz').read_bytes()).splitlines()]
        self.assertEqual(actions[1]['events'][-1]['changes']['assign_to']['after']['uid'],100)

    def test_still_recording_rejected(self):
        self.manifest['recording_state']='recording';self.write_manifest()
        with self.assertRaises(SubmissionError):self.export()

    def test_consent_and_metadata_unknown_field_rejected(self):
        for change in ({'consent_to_share':False},{'unknown':'not accepted'},{'goal':'C:\\Users\\private'}):
            meta=loads(self.metadata.read_bytes());meta.update(change);self.metadata.write_bytes(json_bytes(meta))
            with self.assertRaises(SubmissionError):self.export()
            # Set up a clean metadata value for the next check.
            clean=loads((ROOT/'schemas/metadata.example.json').read_bytes());clean['consent_to_share']=True
            self.metadata.write_bytes(json_bytes(clean))

    def test_edited_bytes_rejected(self):
        directory,_=self.export();(directory/'SUMMARY.md').write_bytes(b'tampered')
        with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_resigned_unknown_nested_field_rejected(self):
        directory,_=self.export();path=directory/'player-observations.ndjson.gz'
        rows=[loads(x) for x in gzip.decompress(path.read_bytes()).splitlines()]
        rows[0]['labor']['units'][0]['pointer']=1234
        path.write_bytes(gzip.compress(b''.join(json_bytes(x).replace(b'\n',b'')+b'\n' for x in rows),mtime=0))
        self.resign(directory)
        with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_claimed_individual_rate_rejected(self):
        directory,_=self.export();path=directory/'player-observations.ndjson.gz'
        rows=[loads(x) for x in gzip.decompress(path.read_bytes()).splitlines()]
        rows[0]['labor']['units'][0]['native_actual_rate']=.6
        path.write_bytes(gzip.compress(b''.join(json.dumps(x).encode()+b'\n' for x in rows),mtime=0))
        self.resign(directory)
        with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_foreign_building_scope_rejected(self):
        directory,_=self.export();path=directory/'player-observations.ndjson.gz'
        rows=[loads(x) for x in gzip.decompress(path.read_bytes()).splitlines()]
        rows[0]['buildings'][0]['zone_id']=999
        path.write_bytes(gzip.compress(b''.join(json.dumps(x).encode()+b'\n' for x in rows),mtime=0))
        self.resign(directory)
        with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_zip_traversal_and_duplicate_member_rejected(self):
        directory,_=self.export()
        for malicious in ('../outside','submission.json'):
            path=self.root/(('traversal' if malicious.startswith('..') else 'duplicate')+'.zip')
            with zipfile.ZipFile(path,'w') as archive:
                for name in FILES:archive.writestr(name,(directory/name).read_bytes())
                archive.writestr(malicious,b'no')
            with self.assertRaises(SubmissionError):validate_submission(path)

    def test_expanded_limit_rejected(self):
        directory,_=self.export()
        with patch('validate_submission.MAX_EXPANDED',1024):
            with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_calendar_boundaries(self):
        self.assertEqual(clock(0),{'year':800,'month':3,'phase':'early','month_second':0})
        self.assertEqual(clock(20)['phase'],'middle')
        self.assertEqual(clock(40)['phase'],'late')
        self.assertEqual(clock(60)['month'],4)
        self.assertEqual(clock(600)['year'],801)
        self.assertEqual(clock(4547)['month'],6)

    def test_duplicate_json_keys_and_nonfinite_rejected(self):
        for raw in ('{"x":1,"x":2}','{"x":NaN}'):
            with self.assertRaises(SubmissionError):loads(raw)

    def test_missing_and_extra_files_rejected(self):
        directory,_=self.export();extra=directory/'private-raw'
        extra.write_text('no')
        with self.assertRaises(SubmissionError):validate_submission(directory)
        extra.unlink();(directory/'health.ndjson.gz').unlink()
        with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_non_ok_player_cannot_carry_new_game_components(self):
        for key in ('labor','population_growth','game_context'):
            with self.subTest(key=key):
                directory,_=self.export()
                def change(rows):
                    payload=rows[0].get(key,{'mode_kind':'Singleplayer'})
                    rows[0]={'status':'rejected',key:payload}
                self.rewrite(directory,'player-observations.ndjson.gz',change)
                with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_unavailable_component_cannot_carry_payload(self):
        for key,status in (('labor','rejected'),('population_growth','unknown')):
            with self.subTest(key=key):
                directory,_=self.export()
                self.rewrite(directory,'player-observations.ndjson.gz',lambda rows:rows[0][key].update(status=status))
                with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_economic_stream_forbids_duplicate_entity_components(self):
        for key in ('labor','population_growth'):
            with self.subTest(key=key):
                directory,_=self.export()
                player=loads(gzip.decompress((directory/'player-observations.ndjson.gz').read_bytes()).splitlines()[0])
                self.rewrite(directory,'economic-snapshots.ndjson.gz',lambda rows:rows[0].update({key:player[key]}))
                with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_economic_cache_cross_session_rejected(self):
        directory,_=self.export()
        self.rewrite(directory,'economic-snapshots.ndjson.gz',lambda rows:rows[0].update(economy={
            'schema_version':1,'status':'ok','source':'native_economic_caches_read_only','session_key':'f'*24}))
        with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_same_segment_rollback_rejected(self):
        directory,_=self.export()
        self.rewrite(directory,'player-observations.ndjson.gz',lambda rows:rows.reverse())
        with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_foreign_action_entity_rejected(self):
        directory,_=self.export()
        self.rewrite(directory,'actions.ndjson.gz',lambda rows:rows[1]['events'][-1]['entity'].update(uid=999))
        with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_incomplete_and_cross_segment_transition_rejected(self):
        directory,_=self.export()
        self.rewrite(directory,'actions.ndjson.gz',lambda rows:rows[-1].update(labor_transitions=[{
            'schema_version':1,'event_type':'labor_transition_milestone','source':'own_unit_sampled_state_transition',
            'unit_uid':999,'transition_id':'labor:test','session_key':'f'*24}]))
        with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_real_tracker_payloads_survive_then_foreign_references_fail(self):
        first=invented_observation(100.);second=invented_observation(101.)
        second['units'][0]['assign_to']=None;second['units'][0]['produce']=None
        transition=LaborTransitionTracker();transition.update(first)
        milestones=transition.update(second)
        growth=GrowthProgressTracker();growth.update(first)
        second['population_growth']['observed_progress_intervals']=growth.update(second)
        self.assertTrue(milestones);self.assertTrue(second['population_growth']['observed_progress_intervals'])
        self.write_rows('player-observations.ndjson',[first,second])
        self.write_rows('actions.ndjson',[{'status':'collector_finished','labor_transitions':milestones}])
        directory,_=self.export()
        value=validate_submission(directory)
        rows=[loads(x) for x in gzip.decompress((directory/'actions.ndjson.gz').read_bytes()).splitlines()]
        self.assertEqual(rows[0]['labor_transitions'][0]['unit_uid'],110)
        self.rewrite(directory,'actions.ndjson.gz',lambda rows:rows[0]['labor_transitions'][0].update(unit_uid=999))
        with self.assertRaises(SubmissionError):validate_submission(directory)
        # Restore an allowed transition, then attack a growth interval's hall identity.
        self.rewrite(directory,'actions.ndjson.gz',lambda rows:rows[0]['labor_transitions'][0].update(unit_uid=110))
        self.rewrite(directory,'player-observations.ndjson.gz',lambda rows:rows[1]['population_growth']['observed_progress_intervals'][0].update(town_hall_uid=999))
        with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_known_zone_and_target_zone_change_pairs_roundtrip(self):
        first=invented_observation(100.);second=invented_observation(101.)
        second['units'][0]['zone_id']=2;second['units'][0]['target_zone_id']=2
        second['labor']['units'][0]['zone_id']=2
        second['labor']['units'][0]['assignment']['same_zone']=False
        self.write_rows('player-observations.ndjson',[first,second])
        differ=ActionDiffer();baseline=differ.feed(first);action=differ.feed(second)
        for row in (baseline,action):
            row.update(session_key='session',capture_run_id='run',capture_segment_id='run:1')
        self.assertTrue(any('zone_id' in event.get('changes',{}) for event in action['events']))
        self.assertTrue(any('target_zone_id' in event.get('changes',{}) for event in action['events']))
        self.write_rows('actions.ndjson',[baseline,action])
        directory,_=self.export()
        self.assertEqual(validate_submission(directory)['metadata']['clan'],'Bear')

    def test_incomplete_growth_interval_rejected(self):
        directory,_=self.export()
        self.rewrite(directory,'player-observations.ndjson.gz',lambda rows:rows[0]['population_growth'].update(
            observed_progress_intervals=[{'session_key':'f'*24,'source':'native_town_hall_progress_difference','town_hall_uid':999}]))
        with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_zip_annotations_and_hidden_prefix_or_suffix_rejected(self):
        directory,good_archive=self.export()
        for variant in ('comment','extra','member_comment','prefix','suffix'):
            path=self.root/(variant+'.zip')
            if variant in ('prefix','suffix'):
                original=good_archive.read_bytes()
                path.write_bytes((b'PRIVATE'+original) if variant=='prefix' else (original+b'PRIVATE'))
            else:
                with zipfile.ZipFile(path,'w') as archive:
                    if variant=='comment':archive.comment=b'PRIVATE'
                    for name in FILES:
                        info=zipfile.ZipInfo(name)
                        if variant=='extra':info.extra=b'\x99\x99\x07\x00PRIVATE'
                        if variant=='member_comment':info.comment=b'PRIVATE'
                        archive.writestr(info,(directory/name).read_bytes())
            with self.subTest(variant=variant):
                with self.assertRaises(SubmissionError):validate_submission(path)

    def test_concatenated_gzip_rejected(self):
        directory,_=self.export();path=directory/'health.ndjson.gz'
        path.write_bytes(path.read_bytes()+gzip.compress(b'{"status":"ok"}\n',mtime=0));self.resign(directory)
        with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_resigned_summary_private_path_rejected(self):
        directory,_=self.export();(directory/'SUMMARY.md').write_text('C:\\Users\\private',encoding='utf-8');self.resign(directory)
        with self.assertRaises(SubmissionError):validate_submission(directory)

    def test_native_multiplayer_cannot_be_relabelled_singleplayer(self):
        rows=[invented_observation(100.),invented_observation(101.)]
        for row in rows:row['game_context']={'mode_kind':'Multiplayer'}
        self.write_rows('player-observations.ndjson',rows)
        with self.assertRaises(SubmissionError):self.export()

    def test_multiple_sessions_require_selection(self):
        rows=[invented_observation(100.),invented_observation(101.)]
        rows[1]['session_key']='different-session'
        self.write_rows('player-observations.ndjson',rows)
        with self.assertRaises(SubmissionError):self.export()

    def test_metadata_example_and_generated_schema_are_current(self):
        from submission_contract import METADATA
        from export_submission_schemas import documents
        template=loads((ROOT/'schemas/metadata.example.json').read_bytes())
        self.assertFalse(template['consent_to_share'])
        with self.assertRaises(SubmissionError):validate(template,METADATA)
        template['consent_to_share']=True
        validate(template,METADATA)
        for name,schema in documents().items():
            document=loads((ROOT/'schemas'/name).read_bytes())
            document.pop('$schema');document.pop('$comment')
            self.assertEqual(document,schema,name)

    def test_workflow_embedded_python_compiles_without_head_imports(self):
        lines=(ROOT/'.github/workflows/validate-submissions.yml').read_text(encoding='utf-8').splitlines()
        count=0;index=0
        while index<len(lines):
            if lines[index].strip()!='run: |':
                index+=1;continue
            index+=1;block=[]
            while index<len(lines) and (not lines[index].strip() or lines[index].startswith('          ')):
                block.append(lines[index][10:]);index+=1
            if block and block[0].startswith("python3 - <<'PY'"):
                body='\n'.join(block[1:]).rstrip()
                self.assertTrue(body.endswith('\nPY'))
                body=body.rsplit('\nPY',1)[0]
                ast.parse(body);count+=1
                self.assertNotIn('candidate/scripts',body)
        self.assertEqual(count,3)

    @unittest.skipUnless(sys.platform=='win32','The collector runtime imports Windows read APIs')
    def test_actual_collect_finish_and_error_tails_export_roundtrip(self):
        from collector import collect
        for end in ('finish','read_error','non_match'):
            with self.subTest(end=end):
                first=invented_observation(100.);second=invented_observation(101.)
                second['units'][0]['assign_to']=None;second['units'][0]['produce']=None
                sequence=[first,second]
                if end=='read_error':sequence.append(PermissionError('invented read failure'))
                if end=='non_match':sequence.append({'status':'not_in_match'})
                sequence.append(KeyboardInterrupt())
                connection=Mock();connection.generation=0
                connection.sample=Mock(side_effect=sequence)
                destination=self.root/('actual-'+end)
                with patch.object(collect,'ProbeConnection',return_value=connection), \
                     patch.object(collect,'local_setup_ready',return_value=True), \
                     patch.object(collect,'project_player_observation',side_effect=lambda row,**kwargs:dict(row)), \
                     patch.object(collect,'reconcile_file',return_value={}), \
                     patch.object(collect.time,'monotonic',side_effect=itertools.count(0.,.1)), \
                     patch.object(collect.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
                    collect.main(['--out-dir',str(destination)])
                source_actions=[loads(line) for line in (destination/'actions.ndjson').read_bytes().splitlines()]
                tails=[row for row in source_actions if row.get('record_kind')=='labor_transition_tail']
                self.assertEqual(len(tails),1)
                self.assertEqual(tails[0]['status'],{'finish':'collector_finished','read_error':'collector_read_error','non_match':'not_in_match'}[end])
                self.assertEqual(tails[0]['session_key'],'session')
                self.assertEqual(tails[0]['game_time_seconds'],101.)
                self.assertEqual(tails[0]['labor_transitions'][0]['phase'],'tracking_incomplete')
                result=prepare(destination,self.metadata,self.root/('review-actual-'+end))
                review=Path(result['review_directory']);validate_submission(review)
                actions=[loads(line) for line in gzip.decompress((review/'actions.ndjson.gz').read_bytes()).splitlines()]
                exported=[row for row in actions if row.get('record_kind')=='labor_transition_tail']
                self.assertEqual(len(exported),1)
                self.assertEqual(exported[0]['game_time_seconds'],101.)
                self.assertEqual(exported[0]['labor_transitions'][0]['phase'],'tracking_incomplete')

    def test_legacy_and_boundary_tails_filter_using_their_own_session(self):
        first=invented_observation(100.);second=invented_observation(101.)
        second['units'][0]['assign_to']=None;second['units'][0]['produce']=None
        tracker=LaborTransitionTracker();tracker.update(first);tracker.update(second)
        tail=tracker.finish()[0]
        other_first=copy.deepcopy(first);other_second=copy.deepcopy(second)
        def replace_id(value):
            if type(value) is dict:
                for key,replacement in (('session_key','other-session'),('capture_run_id','other-run'),('capture_segment_id','other-segment')):
                    if key in value:value[key]=replacement
                for item in value.values():replace_id(item)
            elif type(value) is list:
                for item in value:replace_id(item)
        for row in (other_first,other_second):replace_id(row)
        other_tail=copy.deepcopy(tail);replace_id(other_tail)
        self.write_rows('player-observations.ndjson',[first,second,other_first,other_second])
        for variant in ('legacy','boundary'):
            if variant=='legacy':
                action={'status':'collector_finished','labor_transitions':[tail,other_tail]}
            else:
                action={'record_kind':'baseline','session_key':'other-session','capture_run_id':'other-run',
                    'capture_segment_id':'other-segment','game_time_seconds':100.,'labor_transitions':[tail]}
            self.write_rows('actions.ndjson',[action])
            result=prepare(self.capture,self.metadata,self.root/('review-'+variant),session_key='session')
            directory=Path(result['review_directory']);submission=validate_submission(directory)
            actions=[loads(line) for line in gzip.decompress((directory/'actions.ndjson.gz').read_bytes()).splitlines()]
            self.assertEqual(len(actions),1)
            self.assertEqual(actions[0]['record_kind'],'labor_transition_tail')
            self.assertEqual(actions[0]['session_key'],submission['source_session'])
            self.assertEqual(len(actions[0]['labor_transitions']),1)
            self.assertEqual(actions[0]['labor_transitions'][0]['session_key'],submission['source_session'])
            self.assertEqual(actions[0]['game_time_seconds'],101.)


if __name__=='__main__':unittest.main()
