"""Local, version-gated Northgard data collector. No game controls or uploads."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'research/telemetry'))
sys.path.insert(0, str(ROOT / 'companion/core'))
sys.path.insert(0, str(ROOT / 'scripts'))
from action_capture import ActionProbe, Reader, find_pid
from action_diff import ActionDiffer
from player_observation import project_player_observation
from economy_reconcile import reconcile_stream, summarize_reconciliation
from operation_ledger import OperationLedger
from situation_timeline import SituationTracker
from labor_transitions import LaborTransitionTracker
from growth_projection import GrowthProgressTracker
from base_rings import BaseRingTracker
from local_rules import verify_generated_rules
from create_match_kit import shared_uuid

VERSION = '0.5.0-alpha'
SHARE_FILES = ('player-observations.ndjson', 'economic-snapshots.ndjson',
               'actions.ndjson', 'health.ndjson', 'manifest.json', 'latest.json')
HEALTH_COMPONENTS = ('economy', 'operations', 'map_observation', 'labor', 'population_growth',
                     'match_context', 'base_neighborhood')
_HEALTH_CODE = re.compile(r'[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z')


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def write_line(handle, value):
    handle.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')
    handle.flush()


def component_diagnostics(raw, projected):
    """Source failure and projection rejection are separate, data-free facts."""
    def code(value):
        return value if type(value) is str and _HEALTH_CODE.fullmatch(value) else None
    def codes(values):
        if type(values) is not list:
            return []
        return list(dict.fromkeys(item for item in (code(value) for value in values[:100]) if item))
    result = {}
    for name in HEALTH_COMPONENTS:
        source = raw.get(name, {}) if type(raw) is dict else {}
        target = projected.get(name, {}) if type(projected) is dict else {}
        source = source if type(source) is dict else {}
        target = target if type(target) is dict else {}
        entry = {'source_status': code(source.get('status')) or 'missing',
                 'projection_status': code(target.get('status')) or 'missing',
                 'source_reason_codes': codes(source.get('reason_codes', []) +
                     ([source['reason_code']] if type(source.get('reason_code')) is str else []))
                     if type(source.get('reason_codes', [])) is list else [],
                 'projection_reason_codes': codes(target.get('reason_codes')),
                 'read_guard_issues': codes(source.get('read_guard_issues'))}
        if name == 'base_neighborhood':
            # Rings are computed from this frame's sanitized map, never read
            # from an additional game object or inferred from the previous map.
            entry['source_status'] = 'derived'
            entry['source_reason_codes'] = []
            entry['read_guard_issues'] = []
        if code(source.get('error_type')):
            entry['error_type'] = code(source['error_type'])
        result[name] = entry
    return result


def transition_tail_records(status, transitions):
    """Keep tails on their own segment and last observed game time."""
    identities=('session_key','capture_run_id','capture_segment_id')
    groups={}
    for event in transitions:
        key=tuple(event.get(field) for field in identities)
        if not all(type(value) is str and value for value in key):
            raise ValueError('Incomplete labor tail identity')
        when=event.get('milestone_observed_window',{}).get('observed_by_game_seconds')
        if type(when) not in (int,float) or not math.isfinite(when) or when<0:
            raise ValueError('Incomplete labor tail observation clock')
        groups.setdefault(key,[]).append(event)
    return [{'record_kind':'labor_transition_tail','status':status,
        **dict(zip(identities,key)),
        'game_time_seconds':max(event['milestone_observed_window']['observed_by_game_seconds'] for event in events),
        'labor_transitions':events} for key,events in groups.items()]


def write_transition_tails(handle, status, transitions):
    for record in transition_tail_records(status,transitions):
        write_line(handle,record)


def economic_row(raw, projected, action=None):
    """Only own economy, never raw game/player maps or complete native logs."""
    keys = ('status', 'session_key', 'capture_run_id', 'capture_segment_id',
            'capture_segment_index', 'captured_at', 'game_time_seconds',
            'sample_span_game_seconds', 'clan', 'stocks', 'in_winter', 'population')
    result = {key: projected[key] for key in keys if key in projected}
    # Only the adapter's fixed economic schema is shareable. Arbitrary raw
    # cache slots/counter maps remain exclusively in private research output.
    result['economy'] = projected.get('economy', {'status': 'missing'})
    # Native component errors contain only a type in the shareable channel.
    if result['economy'].get('status') == 'read_error':
        result['economy'] = {key: value for key, value in result['economy'].items() if key != 'error'}
    # Only fresh, own success-event metadata and the state-change interval are
    # needed by the continuous reconciler. Native params never enter this file.
    result['own_success_events'] = []
    result['action_events'] = []
    if action is not None:
        result['own_success_events'] = [{key: event[key] for key in
            ('kind', 'time', 'index', 'session_key', 'capture_run_id', 'capture_segment_id') if key in event}
            for event in action.get('new_native_events', [])]
        observed_interval = action.get('time_interval', {})
        if all(type(observed_interval.get(key)) in (int, float) for key in
               ('start_game_seconds', 'end_game_seconds')):
            for event in result['own_success_events']:
                # Native times are integerized; their first-seen interval also
                # guards a cycle when floor(time) predates the sample boundary.
                event['time_interval'] = {key: observed_interval[key] for key in
                    ('start_game_seconds', 'end_game_seconds')}
        result['action_events'] = [{key: event[key] for key in ('kind', 'time_interval') if key in event}
                                  for event in action.get('events', [])]
    return result


def reconcile_file(path):
    def rows():
        with path.open(encoding='utf-8') as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)
    return summarize_reconciliation(reconcile_stream(rows()))


class ProbeConnection:
    """Revalidate process identity and discard every failed/stale connection.

    A fresh ActionProbe owns a fresh capture identity tracker, so a restarted
    process cannot silently continue the previous run/segment's history.
    """

    def __init__(self, *, pid_finder=None, reader_factory=None, probe_factory=None):
        self.pid_finder = pid_finder or find_pid
        self.reader_factory = reader_factory or Reader
        self.probe_factory = probe_factory or ActionProbe
        self.reader = self.probe = None
        self.generation = 0

    def close(self):
        reader = self.reader
        self.reader = self.probe = None
        if reader is not None:
            reader.close()

    def sample(self):
        try:
            pid = self.pid_finder()
            if self.reader is not None and self.reader.pid != pid:
                self.close()
            if self.probe is None:
                self.reader = self.reader_factory(pid)
                candidate = self.probe_factory(self.reader)
                if not candidate.compatibility.get('data_cdb_verified'):
                    raise ValueError('Collector requires a verified data.cdb profile')
                self.probe = candidate
                self.generation += 1
            return self.probe.sample()
        except BaseException:
            # This also handles an exited process whose PID is subsequently
            # reused: a failing read drops its old handle and metadata caches.
            self.close()
            raise


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=VERSION)
    parser.add_argument('--seconds', type=float, default=None,
                        help='Optional wall-clock test limit (0..14400). Omit to record until game finished or stopped; 0 takes one snapshot.')
    parser.add_argument('--interval', type=float, default=0.5)
    parser.add_argument('--out-dir', type=Path)
    parser.add_argument('--research-raw', action='store_true', help='Private research snapshot file, excluded from normal sharing.')
    parser.add_argument('--shared-match-id', type=shared_uuid,
                        help='Contributor-declared UUIDv4 shared by willing participants in one match.')
    parser.add_argument('--team-label', choices=('A', 'B', 'unknown'),
                        help='Contributor label, independent of the native team ordinal.')
    parser.add_argument('--player-slot', type=int, choices=(1, 2, 3),
                        help='Anonymized seat within the contributor-declared team.')
    parser.add_argument('--declared-format', choices=('3v3', 'unknown'),
                        help='Contributor declaration; never overrides the native mode or roster.')
    return parser


def contributor_context(args, parser):
    if not args.shared_match_id:
        if args.team_label is not None or args.player_slot is not None or args.declared_format is not None:
            parser.error('Team/slot/format labels require --shared-match-id.')
        return None
    team = args.team_label or 'unknown'
    if args.player_slot is not None and team == 'unknown':
        parser.error('--player-slot requires --team-label A or B.')
    return {'shared_match_id': args.shared_match_id,
            'declared_format': args.declared_format or 'unknown',
            'team_label': team, 'player_slot': args.player_slot}


def mode_diagnostics(observation, declared=None):
    context = observation.get('game_context', {})
    match = observation.get('match_context', {})
    mode = context.get('mode_kind') or match.get('native_mode_kind')
    native_format = match.get('configured_team_layout')
    if native_format is None:
        native_format = {'Team2V2': '2v2', 'Team3V3': '3v3', 'Team4V4': '4v4',
                         'Team2V2V2': '2v2v2', 'Team2V2V2V2': '2v2v2v2',
                         'Duel': '1v1'}.get(context.get('mode_variant_kind'))
    link_status = 'not_declared'
    if declared:
        if mode == 'Singleplayer' or (declared['declared_format'] == '3v3'
                and native_format is not None and native_format != '3v3'):
            link_status = 'conflicts_with_native_context'
        elif native_format is None or declared['declared_format'] == 'unknown':
            link_status = 'contributor_declared_native_layout_unverified'
        else:
            link_status = 'format_matches_native_configuration_team_labels_still_self_reported'
    suspected_multiplayer = (mode == 'Multiplayer' or (mode is None and (declared is not None or native_format is not None
        or (type(match.get('native_team_count')) is int and match['native_team_count'] > 1))))
    return {'native_mode_kind': mode,
            'native_configured_format': native_format,
            'capture_validation_stage': ('experimental_multiplayer_unverified' if suspected_multiplayer else
                'experimental_singleplayer_unverified' if mode == 'Singleplayer' else 'native_mode_unverified'),
            'contributor_link_status': link_status}


def local_setup_ready():
    return verify_generated_rules(ROOT)


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argument_parser()
    args = parser.parse_args(argv)
    declared = contributor_context(args, parser)
    if sys.version_info < (3, 11) or sys.platform != 'win32' or struct.calcsize('P') != 8:
        parser.error('Python >=3.11 on 64-bit Windows is required.')
    if not local_setup_ready():
        parser.error('Local rule definitions are missing or changed. Run python scripts/setup_local.py, then scripts/doctor.py.')
    if ((args.seconds is not None and not 0 <= args.seconds <= 14400)
            or not 0.1 <= args.interval <= 10):
        parser.error('Optional seconds must be 0..14400; interval must be 0.1..10.')
    output = args.out_dir or ROOT / 'captures' / (datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8])
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in SHARE_FILES):
        parser.error('Output directory already contains capture files; choose a new directory.')
    manifest = {'schema_version': 1, 'collector_version': VERSION,
                'collection_id': uuid.uuid4().hex, 'started_at': now(),
                'recording_state': 'recording', 'transfer': 'local_only_no_automatic_upload',
                'scope': 'own_player_observation_and_own_economic_caches',
                'interval_wall_seconds_requested': args.interval,
                'duration_wall_seconds_limit': args.seconds,
                'game_time_is_authoritative': True,
                'contributor_context': declared,
                'contributor_context_source': 'participant_declaration_not_native_match_identity',
                'release_live_multiplayer_validation': 'not_run',
                'pause_policy': 'Repeated game times are observations, never extra production seconds.',
                'raw_private_research_enabled': args.research_raw,
                'limitations': ['State sampling is non-atomic and does not capture every input command.',
                                'Unverified visibility outside owned zones is omitted.',
                                'Population rules currently verified for ordinary Bear/Raven only.',
                                'Economic reconciliation covers inspected intervals, not a complete transaction ledger.',
                                'Per-unit native final rates are not persistent caches; labor records inputs, not measured unit income.',
                                'Growth bonus and exact birth eligibility remain incomplete; interval ETA is conditional.',
                                'Multiplayer capture is experimental; release-level short/full 3v3 validation has not run.',
                                'Shared UUID and A/B seats are participant declarations, not verified cross-client identity.',
                                'Allied effects, trades and shared-vision discovery provenance are not fully attributed.',
                                'Base rings use discovered geometric adjacency only; incomplete rings do not establish absent tiles.']}
    atomic_json(output / 'manifest.json', manifest)
    atomic_json(output / 'latest.json', {'status': 'waiting_for_game', 'captured_at': now()})
    handles = {name: (output / name).open('x', encoding='utf-8') for name in SHARE_FILES if name.endswith('.ndjson')}
    private = (output / 'research-raw.ndjson').open('x', encoding='utf-8') if args.research_raw else None
    connection = ProbeConnection()
    differ = ActionDiffer()
    ledger = OperationLedger()
    situations = SituationTracker()
    labor_transitions = LaborTransitionTracker()
    growth_progress = GrowthProgressTracker()
    base_rings = BaseRingTracker()
    counts = Counter()
    component_counts = Counter()
    sequence = 0
    previous_time = None
    last_console = 0.0
    end = None if args.seconds is None else time.monotonic() + args.seconds
    stop_reason = 'collector_error'
    print(json.dumps({'status': 'waiting_for_game', 'output_directory': str(output), 'automatic_upload': False}), flush=True)
    try:
        while True:
            begin = time.monotonic()
            if (output / 'STOP').exists():
                stop_reason = 'stop_file'
                break
            raw = None
            try:
                previous_generation = connection.generation
                raw = connection.sample()
                if connection.generation != previous_generation:
                    previous_time = None
                    manifest['process_connection_count'] = connection.generation
                    manifest['compatibility'] = connection.probe.compatibility
                    manifest['game_build'] = connection.probe.profile['game_build']
                    atomic_json(output / 'manifest.json', manifest)
                projected = project_player_observation(raw, include_own_events=True)
                status = 'ok' if projected.get('status') == 'ok' else raw.get('status', 'invalid_snapshot')
                if raw.get('status') == 'ok' and projected.get('status') != 'ok':
                    status = 'player_observation_rejected'
                if status == 'ok':
                    diagnostics = mode_diagnostics(projected, declared)
                    projected['sequence'] = sequence
                    projected['base_neighborhood'] = base_rings.update(projected)
                    observed_growth = growth_progress.update(projected)
                    if 'population_growth' in projected:
                        projected['population_growth']['observed_progress_intervals'] = observed_growth
                    if 'native_log_checkpoint' in raw:
                        projected['native_log_checkpoint'] = raw['native_log_checkpoint']
                    write_line(handles['player-observations.ndjson'], projected)
                    # Differ only sees the sanitized player observation.
                    diff_input = projected  # ActionDiffer adapts the strict own-event schema.
                    action = differ.feed(diff_input)
                    action.update({key: raw.get(key) for key in ('session_key', 'capture_run_id', 'capture_segment_id')})
                    action['resource_ledger'] = ledger.feed(projected, action)
                    action['situations'] = situations.feed(projected)
                    action['labor_transitions'] = labor_transitions.update(projected)
                    write_line(handles['economic-snapshots.ndjson'], economic_row(raw, projected, action))
                    write_line(handles['actions.ndjson'], action)
                    atomic_json(output / 'latest.json', projected)
                    if private:
                        write_line(private, raw)
                    current_time = raw['game_time_seconds']
                    counts['same_game_time_observations'] += int(previous_time == current_time)
                    previous_time = current_time
                    manifest['last_game_time_seconds'] = current_time
                    manifest['last_clan'] = raw.get('clan')
                    manifest['last_game_context'] = projected.get('game_context', {})
                    manifest['last_match_context'] = projected.get('match_context', {})
                    manifest['last_mode_diagnostics'] = diagnostics
                    manifest['last_population'] = projected.get('population')
                    manifest['last_population_cap'] = projected.get('population_cap')
                    manifest['last_own_unit_entity_count'] = len(projected.get('units', []))
                    manifest['last_own_unit_kind_counts'] = dict(Counter(
                        unit['kind'] for unit in projected.get('units', []) if 'kind' in unit))
                    manifest['last_own_zone_object_kind_counts'] = dict(Counter(
                        item['kind'] for item in projected.get('buildings', []) if 'kind' in item))
                    neighborhood = projected['base_neighborhood']
                    manifest['last_base_neighborhood'] = {
                        'status': neighborhood['status'],
                        'anchor_zone_id': (neighborhood.get('anchor') or {}).get('zone_id'),
                        'known_ring1_count': len(neighborhood['ring1_zone_ids']),
                        'known_ring2_count': len(neighborhood['ring2_zone_ids']),
                        'known_unclassified_count': len(neighborhood['known_unclassified_zone_ids']),
                        'coverage': neighborhood['coverage']}
                    manifest['last_session_key'] = raw.get('session_key')
                    manifest['last_capture_run_id'] = raw.get('capture_run_id')
                    manifest['last_capture_segment_id'] = raw.get('capture_segment_id')
                    for name, state_value in (
                            ('operations', projected.get('operations', {}).get('status', 'missing')),
                            ('map', projected.get('map_observation', {}).get('status', 'missing')),
                            ('base_neighborhood', projected['base_neighborhood']['status']),
                            ('match_context', projected.get('match_context', {}).get('status', 'missing')),
                            ('labor', projected.get('labor', {}).get('status', 'missing')),
                            ('population_growth', projected.get('population_growth', {}).get('status', 'missing')),
                            ('feast_quote', projected.get('operations', {}).get('feast_quote', {}).get('status', 'missing'))):
                        component_counts[name + ':' + state_value] += 1
                    cap_verified = projected.get('economy', {}).get('resources', {}).get('Food', {}).get('stock_cap_verified') is True
                    component_counts['food_capacity:' + ('verified' if cap_verified else 'unverified')] += 1
                    manifest['component_health_counts'] = dict(component_counts)
                    if raw.get('session_game_finished'):
                        stop_reason = 'game_finished'
                else:
                    differ.feed({'status': status})
                    ledger.feed({'status': status})
                    situations.feed({'status': status})
                    interrupted = labor_transitions.update({'status': status})
                    write_transition_tails(handles['actions.ndjson'],status,interrupted)
                    growth_progress.update({'status': status})
                    base_rings.update({'status': status})
                    write_line(handles['economic-snapshots.ndjson'], {'status': status, 'sequence': sequence, 'captured_at': now()})
                    atomic_json(output / 'latest.json', {'status': status, 'captured_at': now()})
                health = {'sequence': sequence, 'captured_at': now(), 'status': status,
                          'game_time_seconds': raw.get('game_time_seconds'),
                          'economy_status': raw.get('economy', {}).get('status'),
                          'operations_status': projected.get('operations', {}).get('status'),
                          'map_status': projected.get('map_observation', {}).get('status'),
                          'base_neighborhood_status': projected.get('base_neighborhood', {}).get('status'),
                          'match_context_status': projected.get('match_context', {}).get('status'),
                          'native_mode_kind': projected.get('game_context', {}).get('mode_kind'),
                          'native_mode_variant': projected.get('game_context', {}).get('mode_variant_kind'),
                          'capture_validation_stage': mode_diagnostics(projected, declared)['capture_validation_stage'],
                          'contributor_link_status': mode_diagnostics(projected, declared)['contributor_link_status'],
                          'labor_status': projected.get('labor', {}).get('status'),
                          'individual_rate_status': projected.get('labor', {}).get('individual_rate_status'),
                          'population_growth_status': projected.get('population_growth', {}).get('status'),
                          'labor_unknown_fields': projected.get('labor', {}).get('unknown_fields', []),
                          'growth_unknown_fields': projected.get('population_growth', {}).get('unknown_fields', []),
                          'food_capacity_verified': projected.get('economy', {}).get('resources', {}).get('Food', {}).get('stock_cap_verified') is True,
                          'feast_quote_status': projected.get('operations', {}).get('feast_quote', {}).get('status'),
                          'observation_rejection_codes': projected.get('reason_codes', []),
                          'sample_span_game_seconds': raw.get('sample_span_game_seconds')}
                health['component_diagnostics'] = component_diagnostics(raw, projected)
            except (ValueError, OSError, KeyError, struct.error, RuntimeError) as error:
                status = 'collector_read_error'
                # No raw exception text, process addresses or installation paths
                # in the shareable channels.
                health = {'sequence': sequence, 'captured_at': now(), 'status': status,
                          'error_type': type(error).__name__}
                message = str(error)
                health['reason_code'] = ('unsupported_bytecode' if 'bytecode differs' in message else
                    'unsupported_game_rules' if 'data.cdb differs' in message else
                    'game_not_running_or_multiple' if 'Expected one Northgard.exe' in message else
                    'process_read_denied' if isinstance(error, PermissionError) else
                    'collector_read_failure')
                differ.feed({'status': status})
                ledger.feed({'status': status})
                situations.feed({'status': status})
                interrupted = labor_transitions.update({'status': status})
                write_transition_tails(handles['actions.ndjson'],status,interrupted)
                growth_progress.update({'status': status})
                base_rings.update({'status': status})
                write_line(handles['economic-snapshots.ndjson'], health)
                atomic_json(output / 'latest.json', health)
                connection.close()
            counts[status] += 1
            write_line(handles['health.ndjson'], health)
            sequence += 1
            if begin - last_console >= 10 or sequence == 1:
                print(json.dumps(health, ensure_ascii=False), flush=True)
                manifest['status_counts'] = dict(counts)
                atomic_json(output / 'manifest.json', manifest)
                last_console = begin
            if stop_reason == 'game_finished':
                break
            if end is not None and time.monotonic() >= end:
                stop_reason = 'single_snapshot' if args.seconds == 0 else 'duration_limit'
                break
            time.sleep(max(0, args.interval - (time.monotonic() - begin)))
    except KeyboardInterrupt:
        stop_reason = 'keyboard_interrupt'
    finally:
        unfinished_transitions = labor_transitions.finish()
        write_transition_tails(handles['actions.ndjson'],'collector_finished',unfinished_transitions)
        for handle in handles.values():
            handle.close()
        if private:
            private.close()
        connection.close()
        manifest.update(recording_state='stopped', stopped_at=now(), stop_reason=stop_reason,
                        status_counts=dict(counts), records_attempted=sequence)
        manifest['economic_reconciliation'] = reconcile_file(output / 'economic-snapshots.ndjson')
        atomic_json(output / 'manifest.json', manifest)
    print(json.dumps({'status': 'stopped', 'stop_reason': stop_reason,
                      'status_counts': dict(counts), 'reconciliation': manifest['economic_reconciliation']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
