"""Create a local reviewable contribution, without network or Git operations."""
from __future__ import annotations

import argparse
from datetime import date
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import uuid
import zipfile

from submission_contract import (DATA_FILES, FILES, MANIFEST_SCHEMA, MAX_COMPRESSED,
    MAX_EXPANDED, MAX_RECORD, METADATA, RECORD_SCHEMAS, SUBMISSION_SCHEMA,
    SubmissionError, clock, digest, json_bytes, loads, project, validate)


def identity(value):
    if type(value) is not str or not value or len(value) > 256:
        raise SubmissionError('Missing capture/session identity')
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]


def pseudonymize(value):
    if type(value) is dict:
        for key in ('session_key','capture_run_id','capture_segment_id','map_instance_id'):
            if key in value:
                value[key] = identity(value[key])
        if 'transition_id' in value:
            value['transition_id']='labor:'+identity(value['transition_id'])
        for item in value.values():
            pseudonymize(item)
    elif type(value) is list:
        for item in value:
            pseudonymize(item)


def source_rows(path):
    if path.is_symlink() or not path.is_file():
        raise SubmissionError('Missing regular capture file: ' + path.name)
    before = path.stat()
    with path.open('rb') as handle:
        while raw := handle.readline(MAX_RECORD+1):
            if len(raw) > MAX_RECORD:
                raise SubmissionError('Source record too large: ' + path.name)
            if raw.strip():
                row = loads(raw)
                if type(row) is not dict:
                    raise SubmissionError('Source record must be an object')
                yield row
    after = path.stat()
    if (before.st_size,before.st_mtime_ns,before.st_ino) != (after.st_size,after.st_mtime_ns,after.st_ino):
        raise SubmissionError('Capture changed during export; stop collector first')


def action_records(original):
    """Bind legacy/end/boundary tails before selecting one capture session."""
    fields=('session_key','capture_run_id','capture_segment_id')
    parent=tuple(original.get(key) for key in fields)
    transitions=original.get('labor_transitions',[])
    if type(transitions) is not list:
        raise SubmissionError('Invalid labor tail list')
    main=dict(original);main['labor_transitions']=[]
    tails={}
    for event in transitions:
        if type(event) is not dict:
            raise SubmissionError('Invalid labor tail')
        key=tuple(event.get(field) for field in fields)
        if not all(type(value) is str and value for value in key):
            raise SubmissionError('Labor tail is missing a capture identity')
        if key==parent:
            main['labor_transitions'].append(event)
        else:
            tails.setdefault(key,[]).append(event)
    if not tails or any(value is not None for value in parent) or not transitions:
        yield main
    elif any(original.get(key) for key in ('events','resource_changes','time_interval')):
        raise SubmissionError('Identity-free action row cannot carry other game payload')
    for key,events in tails.items():
        times=[event.get('milestone_observed_window',{}).get('observed_by_game_seconds') for event in events]
        if not all(type(value) in (int,float) and math.isfinite(value) and 0<=value<2**53 for value in times):
            raise SubmissionError('Labor tail has no valid last observed game clock')
        yield {'record_kind':'labor_transition_tail','status':original.get('status','transition_boundary_observed'),
            **dict(zip(fields,key)),'game_time_seconds':max(times),'labor_transitions':events}


def capture_rows(path, *, actions=False):
    for original in source_rows(path):
        yield from action_records(original) if actions else (original,)


def summarize(metadata, start, end, record_counts):
    start_clock,end_clock = clock(start),clock(end)
    def label(c):
        return f"A{c['year']} 年 {c['month']} 月 · " + {'early':'月初','middle':'月中','late':'月末'}[c['phase']]
    lines = ['# 对局数据贡献审阅', '', '这是本地生成的待审阅数据；本工具没有上传或运行 Git。', '',
        '- 数据来源：贡献者本人游玩的人类玩家视角；未经验证的 AI／敌方信息不接收。',
        f"- 氏族：{metadata['clan'] or '未知'}；模式：{metadata['mode']}；经验：{metadata['experience']}。",
        f'- 覆盖：{label(start_clock)} → {label(end_clock)}；游戏秒 {start:.6f} → {end:.6f}。',
        '- 月初／中／末是每月 20 游戏秒的报告分桶；不是游戏原生阶段。暂停不算产出时间。',
        '- 未读取到的值保留未知。职位／produce 标记不等于逐人实际到账。',
        '- 固定字段投影：不含私有 raw、latest、进程地址、安装路径、姓名、账号或敌方全图。',
        '- 请逐项审阅 submission.json 的目标、标签和反馈，自由文字仍需本人检查。', '',
        '## 游戏目标', '', metadata['goal'], '', '## 记录数', '']
    lines.extend(f'- {name}: {count}' for name,count in record_counts.items())
    lines += ['', '## 玩家经验（不是已证规则）', '']
    for feedback in metadata['feedback']:
        c=clock(feedback['game_time_seconds'])
        lines.extend([f"### {label(c)}（{feedback['game_time_seconds']} 游戏秒；时间精度 {feedback['time_precision']}）", '',
            f"观察：{feedback['observation']}", '', f"推测：{feedback['hypothesis']}", '',
            f"建议：{feedback['suggestion']}", '', f"反例／条件：{feedback['counterexample']}", ''])
    return ('\n'.join(lines)+'\n').encode('utf-8')


def prepare(capture, metadata_path, output, *, session_key=None):
    capture,output = Path(capture).resolve(),Path(output).resolve()
    manifest_path=capture/'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size > MAX_RECORD:
        raise SubmissionError('Missing or oversized capture manifest')
    manifest_raw=manifest_path.read_bytes()
    manifest=loads(manifest_raw)
    if type(manifest) is not dict or manifest.get('recording_state') != 'stopped':
        raise SubmissionError('Stop collection before packaging')
    meta_raw=Path(metadata_path).read_bytes()
    if len(meta_raw) > MAX_RECORD:
        raise SubmissionError('Metadata too large')
    metadata=loads(meta_raw)
    validate(metadata,METADATA)
    sessions=set()
    for row in source_rows(capture/'player-observations.ndjson'):
        if row.get('status') == 'ok':
            if row.get('observation_type') != 'PlayerObservation' or row.get('scope') != 'local_player_and_owned_zones':
                raise SubmissionError('Only verified own-player projections are accepted')
            key=row.get('session_key')
            identity(key)
            sessions.add(key)
    if session_key is None:
        if len(sessions) != 1:
            raise SubmissionError('Capture must have exactly one match; select --session-key explicitly')
        session_key=next(iter(sessions))
    if session_key not in sessions:
        raise SubmissionError('Selected session is not in this capture')
    match_id=str(uuid.uuid4())
    today=date.today().isoformat()
    destination=output/'data'/'submissions'/today[:4]/match_id
    destination.mkdir(parents=True,exist_ok=False)
    files=[]; dropped=[0]; counts={}; times=[]; observed_clans=set(); observed_modes=set(); expanded_total=0
    try:
        for name in DATA_FILES:
            source=capture/name.removesuffix('.gz')
            expanded=count=0
            # mtime=0 and empty filename remove local naming/timestamp metadata.
            with (destination/name).open('xb') as binary:
                with gzip.GzipFile(filename='',mode='wb',fileobj=binary,mtime=0,compresslevel=9) as zipped:
                    for original in capture_rows(source,actions=name=='actions.ndjson.gz'):
                        if original.get('session_key') not in (None,session_key):
                            continue
                        if name.startswith('player-') and original.get('status') == 'ok' and original.get('session_key') != session_key:
                            continue
                        row=project(original,RECORD_SCHEMAS[name],dropped)
                        if name.startswith('player-') and original.get('status') != 'ok':
                            row={key:value for key,value in row.items() if key in
                                 ('status','sequence','game_time_seconds','session_key','capture_run_id','capture_segment_id')}
                        pseudonymize(row)
                        when=row.get('game_time_seconds')
                        if when is None and type(row.get('time_interval')) is dict:
                            when=row['time_interval'].get('end_game_seconds')
                            if when is not None:
                                row['game_time_seconds']=when
                        if type(when) in (int,float):
                            row['game_clock']=clock(when)
                        validate(row,RECORD_SCHEMAS[name])
                        if name.startswith('player-') and row.get('status') == 'ok':
                            if type(when) not in (int,float):
                                raise SubmissionError('Accepted player sample requires a game clock')
                            times.append(when)
                            observed_clans.add(row.get('clan'))
                            context=row.get('game_context',{})
                            if context.get('mode_kind') is not None:
                                observed_modes.add(context['mode_kind'])
                        raw=(json.dumps(row,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n').encode('utf-8')
                        expanded+=len(raw); expanded_total+=len(raw)
                        if expanded > MAX_EXPANDED or expanded_total > 1024*1024*1024:
                            raise SubmissionError('Expanded contribution too large; split capture sessions')
                        zipped.write(raw); count+=1
            compressed=(destination/name).read_bytes()
            if len(compressed) > MAX_COMPRESSED:
                raise SubmissionError('Compressed contribution file exceeds 20 MiB; use a shorter stopped capture')
            files.append({'path':name,'size_bytes':len(compressed),'sha256':digest(compressed),'expanded_bytes':expanded,'record_count':count})
            counts[name]=count
        if not times:
            raise SubmissionError('No accepted player observations')
        if manifest_path.read_bytes()!=manifest_raw:
            raise SubmissionError('Capture manifest changed during export; stop collector first')
        if len(observed_clans) != 1 or None in observed_clans:
            raise SubmissionError('Ambiguous observed clan')
        if observed_modes and observed_modes!={'Singleplayer'}:
            raise SubmissionError('Native context is not verified singleplayer; multiplayer contribution is not supported yet')
        mode_evidence=('native_singleplayer' if observed_modes=={'Singleplayer'} else
            'contributor_declared_native_context_unavailable' if metadata['mode']=='singleplayer' else 'unknown')
        observed_clan=next(iter(observed_clans))
        if metadata['clan'] not in (None,observed_clan):
            raise SubmissionError('Metadata clan disagrees with captured player')
        if metadata['clan'] is None:
            metadata['clan']=observed_clan
        start,end=min(times),max(times)
        for feedback in metadata['feedback']:
            if not start <= feedback['game_time_seconds'] <= end:
                raise SubmissionError('Feedback time outside collected range')
        source_collection=identity(manifest.get('collection_id'))
        shared_manifest={'schema_version':1,'collector_version':manifest.get('collector_version'),
            'recording_state':'stopped','source_collection_id':source_collection,'projection':'submission-core.v1',
            'dropped_source_fields':dropped[0],
            'excluded_files_policy':'fixed_names_only_no_raw_no_latest_no_paths_no_opponents'}
        validate(shared_manifest,MANIFEST_SCHEMA)
        extra={'capture-manifest.json':json_bytes(shared_manifest),'SUMMARY.md':summarize(metadata,start,end,counts)}
        for name,raw in extra.items():
            (destination/name).write_bytes(raw)
            files.append({'path':name,'size_bytes':len(raw),'sha256':digest(raw),'expanded_bytes':len(raw),'record_count':1})
        compatibility=manifest.get('compatibility',{})
        if type(compatibility) is not dict:
            compatibility={}
        submission={'schema_version':1,'artifact_type':'northgard_own_player_submission','match_id':match_id,
            'created_date':today,'metadata':metadata,'collector_version':manifest.get('collector_version'),
            'game_build':manifest.get('game_build'),
            'game_fingerprint':{key:compatibility.get(key) for key in ('hlboot_sha256','data_cdb_sha256')},
            'mode_evidence':mode_evidence,
            'capture_id':source_collection,'source_session':identity(session_key),
            'time_start_game_seconds':start,'time_end_game_seconds':end,
            'clock_policy':'A800_March_epoch_60_game_seconds_per_month_thirds_are_report_buckets',
            'scope':'own_player_core_whitelist_v1_no_opponent_or_raw_memory','files':files,'record_schema_version':1}
        validate(submission,SUBMISSION_SCHEMA)
        (destination/'submission.json').write_bytes(json_bytes(submission))
        checksum_lines=[]
        for name in FILES:
            if name != 'checksums.sha256':
                checksum_lines.append(digest((destination/name).read_bytes())+'  '+name)
        (destination/'checksums.sha256').write_text('\n'.join(checksum_lines)+'\n',encoding='utf-8')
        from validate_submission import validate_submission
        validate_submission(destination)
        archive=output/(match_id+'.zip')
        with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_STORED) as zipped:
            for name in FILES:
                zipped.write(destination/name,name)
        return {'match_id':match_id,'review_directory':str(destination),'review_zip':str(archive),
                'upload_performed':False,'records':counts,'dropped_source_fields':dropped[0]}
    except Exception:
        # Keep partial local artifacts for inspection; never recursively delete user data.
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',type=Path,required=True)
    parser.add_argument('--metadata',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--session-key')
    args=parser.parse_args()
    try:
        print(json.dumps(prepare(args.capture,args.metadata,args.output,session_key=args.session_key),ensure_ascii=False))
    except (SubmissionError,OSError,ValueError) as error:
        parser.exit(2,'Submission preparation failed: '+str(error)+'\n')


if __name__ == '__main__':
    main()
