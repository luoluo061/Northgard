"""File sharing locks affect local snapshots, never synthetic capture streams."""
from copy import deepcopy
import contextlib
import io
import itertools
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from collector import collect
from test_base_ring_collector import observation


class AtomicPublicationTests(unittest.TestCase):
    def test_transient_replace_permission_error_retries_and_publishes(self):
        original_replace=Path.replace
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'latest.json'
            target.write_text('{"status":"old"}',encoding='utf-8')
            attempts=[]

            def replace(source,destination):
                attempts.append((source,destination))
                if len(attempts)==1:
                    raise PermissionError('synthetic local sharing lock')
                return original_replace(source,destination)

            with patch.object(Path,'replace',new=replace),patch.object(collect.time,'sleep'):
                published=collect.atomic_json(target,{'status':'new','game_time_seconds':10})
            self.assertIs(published,True)
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')),
                             {'status':'new','game_time_seconds':10})
            self.assertGreaterEqual(len(attempts),2)
            self.assertLessEqual(len(attempts),10)
            self.assertTrue(all(Path(destination)==target for _,destination in attempts))

    def test_permanent_replace_permission_error_is_bounded_and_preserves_old_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'latest.json'
            original=b'{"status":"old","game_time_seconds":9}'
            target.write_bytes(original)
            with patch.object(Path,'replace',side_effect=PermissionError('synthetic local sharing lock')) as replace,\
                    patch.object(collect.time,'sleep'):
                published=collect.atomic_json(target,{'status':'new','game_time_seconds':10})
            self.assertIs(published,False)
            self.assertEqual(target.read_bytes(),original)
            self.assertGreaterEqual(replace.call_count,2)
            self.assertLessEqual(replace.call_count,10)

    def run_optional_publication_failure(self, *, staging_error=None, final_error=None,
                                         final_replace_error=False, aliased_output=False):
        original_replace=Path.replace;original_write_text=Path.write_text;original_open=Path.open
        with tempfile.TemporaryDirectory() as folder:
            requested_output=Path(folder)/'synthetic-capture'
            if aliased_output:
                # Exercise another spelling of the same destination without
                # symlinks or elevated permissions. CI TEMP can likewise use
                # an 8.3 alias while main resolves its output to the long path.
                alias_parent=Path(folder)/'existing-alias-parent'
                alias_parent.mkdir()
                requested_output=alias_parent/'..'/'synthetic-capture'
            output=requested_output.resolve()
            connection=SimpleNamespace(generation=1,close=Mock(),
                probe=SimpleNamespace(compatibility={},profile={'game_build':'synthetic'}))
            rows=[observation(10),observation(11,finished=True)]
            samples=[];locked_attempts=[];append_handles=[]
            final_failure=final_error is not None or final_replace_error

            def open_path(path,*args,**kwargs):
                handle=original_open(path,*args,**kwargs)
                mode=args[0] if args else kwargs.get('mode','r')
                if path.parent==output and path.name.endswith('.ndjson') and mode=='x':
                    append_handles.append(handle)
                return handle

            def sample():
                # A snapshot lock must never close an otherwise healthy reader.
                self.assertEqual(connection.close.call_count,0)
                if samples:
                    for name in ('player-observations.ndjson','economic-snapshots.ndjson','actions.ndjson','research-raw.ndjson'):
                        self.assertEqual(len((output/name).read_text(encoding='utf-8').splitlines()),1)
                samples.append(len(samples))
                return deepcopy(rows[samples[-1]])

            def replace(source,destination):
                target=Path(destination)
                final_manifest=(target.name=='manifest.json' and
                                json.loads(source.read_text(encoding='utf-8')).get('recording_state')=='stopped')
                if final_manifest and final_replace_error:
                    self.assertTrue(append_handles and all(handle.closed for handle in append_handles))
                    self.assertEqual(connection.close.call_count,1)
                    raise PermissionError('synthetic final manifest sharing lock')
                if samples and target.name in ('latest.json','manifest.json') and not final_manifest and staging_error is None:
                    locked_attempts.append(target.name)
                    raise PermissionError('synthetic local sharing lock')
                return original_replace(source,destination)

            def write_text(path,data,*args,**kwargs):
                if samples and path.name in ('latest.json.tmp','manifest.json.tmp'):
                    final_manifest=(path.name=='manifest.json.tmp' and
                                    json.loads(data).get('recording_state')=='stopped')
                    if final_manifest and final_error is not None:
                        self.assertTrue(append_handles and all(handle.closed for handle in append_handles))
                        self.assertEqual(connection.close.call_count,1)
                        raise final_error('synthetic final manifest staging failure')
                    if not final_manifest and staging_error is not None:
                        locked_attempts.append(path.name.removesuffix('.tmp'))
                        raise staging_error('synthetic optional staging failure')
                return original_write_text(path,data,*args,**kwargs)

            def reconcile(_):
                self.assertEqual(len(append_handles),5)
                self.assertTrue(all(handle.closed for handle in append_handles))
                self.assertEqual(connection.close.call_count,1)
                return {}

            connection.sample=sample
            stdout=io.StringIO()
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(collect,'ProbeConnection',return_value=connection))
                stack.enter_context(patch.object(collect,'local_setup_ready',return_value=True))
                stack.enter_context(patch.object(collect,'project_player_observation',side_effect=lambda row,**_:row))
                stack.enter_context(patch.object(collect,'ActionDiffer',
                    return_value=SimpleNamespace(feed=lambda _:{})))
                stack.enter_context(patch.object(collect,'reconcile_file',side_effect=reconcile))
                stack.enter_context(patch.object(collect.time,'monotonic',side_effect=itertools.count(0,.1)))
                stack.enter_context(patch.object(collect.time,'sleep'))
                stack.enter_context(patch.object(Path,'replace',new=replace))
                stack.enter_context(patch.object(Path,'write_text',new=write_text))
                stack.enter_context(patch.object(Path,'open',new=open_path))
                stack.enter_context(contextlib.redirect_stdout(stdout))
                if final_failure:
                    with self.assertRaisesRegex(OSError,'Required local capture metadata publication failed'):
                        collect.main(['--out-dir',str(requested_output),'--research-raw'])
                else:
                    collect.main(['--out-dir',str(requested_output),'--research-raw'])
            self.assertEqual(len(samples),2)
            self.assertEqual(connection.close.call_count,1)
            streams={name:[json.loads(line) for line in (output/name).read_text(encoding='utf-8').splitlines()]
                     for name in ('player-observations.ndjson','economic-snapshots.ndjson','actions.ndjson','health.ndjson','research-raw.ndjson')}
            self.assertEqual([row['game_time_seconds'] for row in streams['player-observations.ndjson']],[10,11])
            self.assertEqual([row['game_time_seconds'] for row in streams['economic-snapshots.ndjson']],[10,11])
            self.assertEqual(len(streams['actions.ndjson']),2)
            self.assertEqual([row['game_time_seconds'] for row in streams['research-raw.ndjson']],[10,11])
            self.assertEqual([row['status'] for row in streams['health.ndjson']],['ok','ok'])
            self.assertEqual([row['local_publication_status'] for row in streams['health.ndjson']],['degraded','degraded'])
            self.assertTrue(all(row.get('reason_code')!='process_read_denied' for row in streams['health.ndjson']))
            self.assertEqual(json.loads((output/'latest.json').read_text(encoding='utf-8'))['status'],'waiting_for_game')
            self.assertEqual(json.loads((output/'manifest.json').read_text(encoding='utf-8'))['recording_state'],
                             'recording' if final_failure else 'stopped')
            if final_failure:
                console=[json.loads(line) for line in stdout.getvalue().splitlines()]
                self.assertFalse(any(row.get('status')=='stopped' for row in console))
                self.assertTrue(all(handle.closed for handle in append_handles))
            self.assertIn('latest.json',locked_attempts);self.assertIn('manifest.json',locked_attempts)

    def test_optional_publication_locks_preserve_all_successful_streams_and_connection(self):
        for aliased_output in (False,True):
            with self.subTest(aliased_output=aliased_output):
                self.run_optional_publication_failure(aliased_output=aliased_output)

    def test_optional_staging_permission_error_does_not_become_a_process_read_failure(self):
        self.run_optional_publication_failure(staging_error=PermissionError)

    def test_optional_staging_oserror_preserves_append_streams_and_connection(self):
        self.run_optional_publication_failure(staging_error=OSError)

    def test_final_required_staging_failure_keeps_streams_closed_and_manifest_unfinalized(self):
        for error in (PermissionError,OSError):
            with self.subTest(error=error.__name__):
                self.run_optional_publication_failure(final_error=error)

    def test_final_required_replace_lock_does_not_claim_successful_finalization(self):
        self.run_optional_publication_failure(final_replace_error=True)

    def test_initial_manifest_publication_failure_stops_before_connecting_or_opening_streams(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'synthetic-capture'
            with patch.object(collect,'atomic_json',return_value=False),\
                    patch.object(collect,'local_setup_ready',return_value=True),\
                    patch.object(collect,'ProbeConnection') as connection,\
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(OSError,'Required local capture metadata publication failed'):
                    collect.main(['--out-dir',str(output)])
            connection.assert_not_called()
            self.assertEqual(list(output.glob('*.ndjson')),[])

    def test_initial_required_staging_failure_is_explicit_and_opens_no_stream(self):
        for error in (PermissionError,OSError):
            with self.subTest(error=error.__name__),tempfile.TemporaryDirectory() as folder:
                output=Path(folder)/'synthetic-capture'
                with patch.object(Path,'write_text',side_effect=error('synthetic initial staging failure')),\
                        patch.object(collect,'local_setup_ready',return_value=True),\
                        patch.object(collect,'ProbeConnection') as connection:
                    with self.assertRaisesRegex(OSError,'Required local capture metadata publication failed'):
                        collect.main(['--out-dir',str(output)])
                connection.assert_not_called()
                self.assertEqual(list(output.glob('*.ndjson')),[])
                self.assertFalse((output/'manifest.json').exists())

    def test_serialization_errors_propagate_without_becoming_deferred_io(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'latest.json'
            target.write_text('{"status":"old"}',encoding='utf-8')
            for invalid,error in ((float('nan'),ValueError),(object(),TypeError)):
                with self.subTest(error=error.__name__):
                    publisher=collect.LocalPublications()
                    with self.assertRaises(error):
                        publisher.write(target,{'invalid':invalid})
                    self.assertEqual(json.loads(target.read_text(encoding='utf-8')),{'status':'old'})
                    self.assertEqual(publisher.status,'ok')
                    self.assertEqual(dict(publisher.deferred_counts),{})


if __name__=='__main__':
    unittest.main()
