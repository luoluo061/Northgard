"""Fail-closed validation of one local contribution or the repository data tree."""
from __future__ import annotations

import argparse
import gzip
import io
import json
from pathlib import Path
import re
import stat
import struct
import zipfile
import zlib

from submission_contract import (DATA_FILES,FILES,MANIFEST_SCHEMA,MAX_COMPRESSED,MAX_EXPANDED,
    MAX_RECORD,MAX_TOTAL_EXPANDED,RECORD_SCHEMAS,SUBMISSION_SCHEMA,SubmissionError,clock,digest,loads,validate)


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise SubmissionError('Only regular files are accepted')


def check_action_references(value,history):
    if type(value) is dict:
        for key,item in value.items():
            if key in ('uid','building_uid') and item is not None and item not in history['entities']:
                raise SubmissionError('Action reference outside own entity history')
            if key in ('zone_id','target_zone_id'):
                locations=item.values() if type(item) is dict else (item,)
                if any(location is not None and (type(location) is not int or location not in history['zones'])
                       for location in locations):
                    raise SubmissionError('Action location outside historical knowledge')
            if key=='assigned_unit_ids':
                pairs=item.values() if type(item) is dict else (item,)
                if any(ids is not None and not set(ids).issubset(history['units']) for ids in pairs):
                    raise SubmissionError('Action worker list outside own history')
            if key=='discovered_zone_ids' and type(item) is dict and item.get('added') not in history['zones']:
                raise SubmissionError('Action discovery outside own knowledge')
            check_action_references(item,history)
    elif type(value) is list:
        for item in value:
            check_action_references(item,history)


def member_data(path):
    """Bounded bytes, no extraction; ZIP names have an exact flat allowlist."""
    if path.is_dir():
        if path.is_symlink() or set(p.name for p in path.iterdir()) != set(FILES):
            raise SubmissionError('Unexpected contribution directory member')
        output={}
        for name in FILES:
            target=path/name
            regular(target)
            limit=MAX_COMPRESSED if name in DATA_FILES else MAX_RECORD
            if target.stat().st_size > limit:
                raise SubmissionError('Contribution member too large')
            output[name]=target.read_bytes()
        return output
    regular(path)
    if path.stat().st_size > MAX_COMPRESSED*len(DATA_FILES)+8*MAX_RECORD:
        raise SubmissionError('Review archive too large')
    with zipfile.ZipFile(path) as archive:
        entries=archive.infolist()
        if [e.filename for e in entries] != list(FILES) or archive.comment:
            raise SubmissionError('Unsafe, duplicate, or unexpected ZIP member')
        output={}
        cursor=0
        for entry in entries:
            if entry.flag_bits or stat.S_ISLNK(entry.external_attr>>16) or entry.extra or entry.comment:
                raise SubmissionError('Noncanonical, encrypted, symbolic or annotated ZIP member rejected')
            if entry.compress_type!=zipfile.ZIP_STORED or entry.header_offset!=cursor:
                raise SubmissionError('Hidden ZIP prefix/gap or outer compression rejected')
            with path.open('rb') as binary:
                binary.seek(cursor)
                header=binary.read(30)
                if len(header)!=30 or header[:4]!=b'PK\x03\x04':
                    raise SubmissionError('Invalid ZIP local header')
                fields=struct.unpack('<4s5H3I2H',header)
                filename_length,extra_length=fields[-2:]
                if extra_length or filename_length!=len(entry.filename) or binary.read(filename_length)!=entry.filename.encode('ascii'):
                    raise SubmissionError('ZIP local fields differ from canonical member')
                if fields[2] or fields[3] or fields[7]!=entry.compress_size or fields[8]!=entry.file_size:
                    raise SubmissionError('ZIP local sizes/flags differ from central directory')
            cursor+=30+len(entry.filename)+entry.file_size
            limit=MAX_COMPRESSED if entry.filename in DATA_FILES else MAX_RECORD
            if entry.file_size > limit or entry.compress_size > limit:
                raise SubmissionError('ZIP expanded size too large')
            with archive.open(entry) as handle:
                raw=handle.read(limit+1)
            if len(raw) > limit:
                raise SubmissionError('ZIP member exceeds size limit')
            output[entry.filename]=raw
        expected_size=cursor+sum(46+len(entry.filename) for entry in entries)+22
        if archive.start_dir!=cursor or path.stat().st_size!=expected_size:
            raise SubmissionError('Hidden ZIP central/trailing data rejected')
        return output


def validate_observation_scope(row):
    if row.get('status') != 'ok':
        if set(row)-{'status','sequence','game_time_seconds','game_clock','session_key','capture_run_id','capture_segment_id'}:
            raise SubmissionError('Rejected observation carries game data')
        return
    if row.get('observation_type') != 'PlayerObservation' or row.get('scope') != 'local_player_and_owned_zones':
        raise SubmissionError('Observation scope is not the own-player contract')
    required={'status','observation_type','scope','local_player_entity_uid','game_time_seconds','session_key',
              'capture_run_id','capture_segment_id','clan','stocks','owned_zone_ids','discovered_zone_ids','units','buildings'}
    if required-set(row) or row['local_player_entity_uid']<=0:
        raise SubmissionError('Accepted player observation is missing own-state provenance')
    if any(len(row[key])!=len(set(row[key])) for key in ('owned_zone_ids','discovered_zone_ids')):
        raise SubmissionError('Duplicate known/owned zone identifier')
    own=set(row.get('owned_zone_ids',[]));known=own|set(row.get('discovered_zone_ids',[]))
    units=row.get('units',[]); buildings=row.get('buildings',[])
    ids=[v['uid'] for v in units+buildings]
    if len(ids) != len(set(ids)):
        raise SubmissionError('Duplicate entity UID')
    entity_ids=set(ids)
    unit_by_id={u['uid']:u for u in units};building_by_id={b['uid']:b for b in buildings}
    for building in buildings:
        if building['zone_id'] not in own or not set(building.get('assigned_unit_ids',[])).issubset({u['uid'] for u in units}):
            raise SubmissionError('Building outside owned scope')
    for unit in units:
        if unit.get('zone_id') is not None and unit['zone_id'] not in known:
            raise SubmissionError('Unit location outside historical known scope')
        for key in ('assign_to','previous_assign_to','going_to'):
            ref=unit.get(key)
            if ref and ((ref.get('uid') is not None and ref['uid'] not in entity_ids)
                        or (ref.get('zone_id') is not None and ref['zone_id'] not in known)):
                raise SubmissionError('Entity reference outside own/known scope')
    for event in row.get('own_events',[]):
        if event.get('actor') != 'self' or ('zone_id' in event and event['zone_id'] not in known):
            raise SubmissionError('Native event outside own scope')
    for component_name in ('economy','labor','population_growth','operations','map_observation'):
        component=row.get(component_name)
        if not component:
            continue
        if component.get('status') not in ('ok','partial','read_race'):
            diagnostic_keys={'schema_version','status','source','scope','unknown_fields','reason_codes','atomic',
                'native_individual_rates_available','actual_settled_amounts_available','individual_rate_status','complete_transaction_ledger'}
            if set(component)-diagnostic_keys:
                raise SubmissionError('Unavailable component carries game payload')
            continue
        required={'schema_version','status','source','session_key','capture_run_id','capture_segment_id','game_time_seconds'}
        if component_name!='economy':
            required.add('local_player_entity_uid')
        if required-set(component):
            raise SubmissionError('Active component is missing source/identity/clock')
        if any(component.get(key)!=row.get(key) for key in ('session_key','capture_run_id','capture_segment_id')):
            raise SubmissionError('Component identity differs from parent')
        if ('local_player_entity_uid' in component
                and component['local_player_entity_uid']!=row.get('local_player_entity_uid')):
            raise SubmissionError('Component local player differs from parent')
        if component_name=='labor':
            for unit in component.get('units',[]):
                canonical=unit_by_id.get(unit['uid'])
                if canonical is None or unit['kind']!=canonical['kind'] or unit['zone_id']!=canonical.get('zone_id'):
                    raise SubmissionError('Labor unit outside parent own scope')
                assignment=unit.get('assignment')
                if assignment:
                    target=building_by_id.get(assignment['building_uid'])
                    if target is None or assignment['zone_id']!=target['zone_id']:
                        raise SubmissionError('Labor assignment outside own buildings')
            for building in component.get('buildings',[]):
                canonical=building_by_id.get(building['uid'])
                if canonical is None or building['kind']!=canonical['kind'] or building['zone_id']!=canonical['zone_id']:
                    raise SubmissionError('Labor building outside own scope')
            if any(z['zone_id'] not in own for z in component.get('zones',[])):
                raise SubmissionError('Labor zone outside own scope')
        elif component_name=='population_growth':
            for hall in component.get('town_halls',[]):
                canonical=building_by_id.get(hall['uid'])
                if canonical is None or canonical['kind']!='TownHall' or hall['zone_id']!=canonical['zone_id']:
                    raise SubmissionError('Growth hall outside own scope')
            halls={hall['uid'] for hall in component.get('town_halls',[])}
            for interval in component.get('observed_progress_intervals',[]):
                if interval['town_hall_uid'] not in halls or any(interval[key]!=row.get(key) for key in ('session_key','capture_run_id','capture_segment_id')):
                    raise SubmissionError('Growth interval outside own hall/segment scope')
                if not interval['start_game_seconds']<interval['end_game_seconds']<=component['game_time_seconds']:
                    raise SubmissionError('Growth interval has invalid bounds')
        elif component_name=='map_observation':
            if component.get('map_identity',{}).get('map_instance_id')!=row.get('session_key'):
                raise SubmissionError('Map instance differs from parent')
            for zone in component.get('known_zones',[]):
                if zone['id'] not in known or not set(zone['known_geometric_neighbor_ids']).issubset(known):
                    raise SubmissionError('Map cell outside historical knowledge')
        elif component_name=='operations':
            if any(z['zone_id'] not in known for z in component.get('colonizations',[])):
                raise SubmissionError('Colonization outside historical knowledge')
            for building in component.get('building_costs',[]):
                canonical=building_by_id.get(building['uid'])
                if canonical is None or building['zone_id']!=canonical['zone_id'] or building['kind']!=canonical['kind']:
                    raise SubmissionError('Cost record outside own building scope')


def validate_gzip_wrapper(raw):
    """A single canonical gzip member: no hidden filename/comment/timestamp."""
    if len(raw)<18 or raw[:4]!=b'\x1f\x8b\x08\x00' or raw[4:8]!=b'\0\0\0\0':
        raise SubmissionError('Noncanonical GZIP header: regenerate with the export tool')
    decoder=zlib.decompressobj(wbits=31)
    expanded=0
    try:
        for offset in range(0,len(raw),65536):
            pending=raw[offset:offset+65536]
            while pending:
                output=decoder.decompress(pending,65536)
                expanded+=len(output)
                if expanded>MAX_EXPANDED:
                    raise SubmissionError('GZIP expanded file limit exceeded')
                pending=decoder.unconsumed_tail
                if decoder.eof:
                    if decoder.unused_data or offset+65536<len(raw):
                        raise SubmissionError('Trailing or concatenated GZIP members rejected')
                    break
        if not decoder.eof:
            raise SubmissionError('Truncated GZIP member')
    except zlib.error as error:
        raise SubmissionError('Invalid GZIP member') from error


def validate_submission(path):
    path=Path(path)
    payload=member_data(path)
    submission=loads(payload['submission.json'])
    validate(submission,SUBMISSION_SCHEMA)
    shared=loads(payload['capture-manifest.json']);validate(shared,MANIFEST_SCHEMA)
    validate(payload['SUMMARY.md'].decode('utf-8'),{'type':'string','maxLength':MAX_RECORD})
    if shared['collector_version'] != submission['collector_version'] or shared['source_collection_id'] != submission['capture_id']:
        raise SubmissionError('Capture/submission manifests disagree')
    checksum_lines=payload['checksums.sha256'].decode('utf-8').splitlines()
    expected={name:digest(raw) for name,raw in payload.items() if name!='checksums.sha256'}
    observed={}
    for line in checksum_lines:
        match=re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9._-]+)',line)
        if not match or match[2] in observed:
            raise SubmissionError('Invalid checksum file')
        observed[match[2]]=match[1]
    if expected != observed:
        raise SubmissionError('Checksum mismatch')
    file_entries={e['path']:e for e in submission['files']}
    if len(file_entries)!=6 or set(file_entries)!=set(DATA_FILES)|{'capture-manifest.json','SUMMARY.md'}:
        raise SubmissionError('Invalid file manifest')
    total=0; player_times=[];observed_clans=set();observed_modes=set();last_times={};histories={}
    for name in (*DATA_FILES,'capture-manifest.json','SUMMARY.md'):
        entry=file_entries[name]
        raw=payload[name]
        if entry['size_bytes']!=len(raw) or entry['sha256']!=digest(raw):
            raise SubmissionError('File manifest mismatch')
        expanded=count=0
        if name in DATA_FILES:
            validate_gzip_wrapper(raw)
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(raw),mode='rb') as handle:
                    while line:=handle.readline(MAX_RECORD+1):
                        expanded+=len(line);total+=len(line)
                        if len(line)>MAX_RECORD or expanded>MAX_EXPANDED or total>MAX_TOTAL_EXPANDED:
                            raise SubmissionError('GZIP expanded record/data limit exceeded')
                        if not line.strip():
                            raise SubmissionError('Blank NDJSON record')
                        row=loads(line);validate(row,RECORD_SCHEMAS[name]);count+=1
                        when=row.get('game_time_seconds')
                        if when is not None and row.get('game_clock') != clock(when):
                            raise SubmissionError('Game calendar disagrees with native clock')
                        if row.get('session_key') not in (None,submission['source_session']):
                            raise SubmissionError('Multiple sessions in one match')
                        economy=row.get('economy')
                        if economy and economy.get('status') in ('ok','read_race'):
                            if any(economy.get(key)!=row.get(key) for key in ('session_key','capture_run_id','capture_segment_id')):
                                raise SubmissionError('Economic cache identity differs from containing sample')
                        if name.startswith('player-'):
                            validate_observation_scope(row)
                            if row.get('status')=='ok':
                                if when is None:
                                    raise SubmissionError('Missing player clock')
                                identity_key=tuple(row.get(key) for key in ('session_key','capture_run_id','capture_segment_id'))
                                if None in identity_key:
                                    raise SubmissionError('Missing accepted player capture identity')
                                if identity_key in last_times and when<last_times[identity_key]:
                                    raise SubmissionError('Game clock rollback inside the same segment')
                                last_times[identity_key]=when
                                history=histories.setdefault(identity_key,{'entities':set(),'units':set(),'zones':set()})
                                history['entities'].update(v['uid'] for v in row.get('units',[])+row.get('buildings',[]))
                                history['units'].update(v['uid'] for v in row.get('units',[]))
                                history['zones'].update(row.get('owned_zone_ids',[])+row.get('discovered_zone_ids',[]))
                                player_times.append(when);observed_clans.add(row.get('clan'))
                                context=row.get('game_context',{})
                                if context.get('mode_kind') is not None:
                                    observed_modes.add(context['mode_kind'])
                        elif name=='actions.ndjson.gz':
                            parent_identity=tuple(row.get(key) for key in ('session_key','capture_run_id','capture_segment_id'))
                            has_payload=bool(row.get('events') or row.get('resource_changes') or row.get('labor_transitions')
                                or row.get('record_kind') in ('baseline','interval','labor_transition_tail'))
                            if has_payload and (None in parent_identity or parent_identity not in histories or when is None):
                                raise SubmissionError('Action payload requires its own observed segment and game clock')
                            for event in row.get('events',[]):
                                history=histories.get(parent_identity)
                                if history is None:
                                    raise SubmissionError('Action interval has no own-player source segment')
                                for field in ('entity','resulting_building'):
                                    entity=event.get(field)
                                    if entity and entity.get('uid') not in history['entities']:
                                        raise SubmissionError('Action entity outside own-player history')
                                check_action_references(event,history)
                            for transition in row.get('labor_transitions',[]):
                                identity_key=tuple(transition[key] for key in ('session_key','capture_run_id','capture_segment_id'))
                                history=histories.get(identity_key)
                                if history is None or transition['unit_uid'] not in history['units']:
                                    raise SubmissionError('Labor transition outside own-player history')
                                if None not in parent_identity and identity_key!=parent_identity:
                                    raise SubmissionError('Labor transition differs from containing segment')
                                for key in ('assignment_observed_window','milestone_observed_window'):
                                    bounds=transition[key]
                                    if (bounds['previous_game_seconds']>bounds['observed_by_game_seconds'] or
                                        abs(bounds['sample_interval_game_seconds']-(bounds['observed_by_game_seconds']-bounds['previous_game_seconds']))>1e-7):
                                        raise SubmissionError('Invalid labor milestone window')
                                elapsed=transition['elapsed_from_assignment_game_seconds_bounds']
                                if elapsed is not None and elapsed['lower']>elapsed['upper']:
                                    raise SubmissionError('Invalid labor elapsed uncertainty bounds')
                                check_action_references(transition,history)
            except (OSError,EOFError) as error:
                raise SubmissionError('Invalid compressed stream') from error
        else:
            expanded,count=len(raw),1
        if expanded!=entry['expanded_bytes'] or count!=entry['record_count']:
            raise SubmissionError('Expanded bytes or record count mismatch')
    if not player_times or min(player_times)!=submission['time_start_game_seconds'] or max(player_times)!=submission['time_end_game_seconds']:
        raise SubmissionError('Player coverage disagrees with manifest')
    if observed_clans != {submission['metadata']['clan']}:
        raise SubmissionError('Clan differs from metadata')
    if observed_modes and observed_modes!={'Singleplayer'}:
        raise SubmissionError('Native context conflicts with supported singleplayer contribution')
    mode_evidence=('native_singleplayer' if observed_modes=={'Singleplayer'} else
        'contributor_declared_native_context_unavailable' if submission['metadata']['mode']=='singleplayer' else 'unknown')
    if submission['mode_evidence']!=mode_evidence:
        raise SubmissionError('Mode evidence does not match native context/declaration')
    for feedback in submission['metadata']['feedback']:
        if not min(player_times)<=feedback['game_time_seconds']<=max(player_times):
            raise SubmissionError('Feedback outside coverage')
    return submission


def validate_tree(root):
    root=Path(root)
    if not root.exists():
        return []
    if root.is_symlink():
        raise SubmissionError('Data root must not be a link')
    output=[];identities=set()
    for year in sorted(root.iterdir()):
        if year.name=='.gitkeep' and year.is_file() and not year.is_symlink() and year.stat().st_size==0:
            continue
        if year.name == 'README.md' and year.is_file() and not year.is_symlink():
            continue
        if year.is_symlink() or not year.is_dir() or not re.fullmatch(r'20[0-9]{2}',year.name):
            raise SubmissionError('Unexpected data year path')
        for match in sorted(year.iterdir()):
            if match.is_symlink() or not match.is_dir():
                raise SubmissionError('Match must be a directory')
            item=validate_submission(match)
            if match.name!=item['match_id'] or year.name!=item['created_date'][:4]:
                raise SubmissionError('Match path disagrees with submission ID/date')
            key=(item['capture_id'],item['source_session'])
            if key in identities:
                raise SubmissionError('Duplicate capture/session contribution')
            identities.add(key);output.append(item)
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path',type=Path)
    parser.add_argument('--tree',action='store_true')
    args=parser.parse_args()
    try:
        result=validate_tree(args.path) if args.tree else [validate_submission(args.path)]
        print(json.dumps({'valid':True,'match_count':len(result),'match_ids':[x['match_id'] for x in result]}))
    except (SubmissionError,OSError,ValueError,zipfile.BadZipFile) as error:
        parser.exit(2,'Submission validation failed: '+str(error)+'\n')


if __name__=='__main__':
    main()
