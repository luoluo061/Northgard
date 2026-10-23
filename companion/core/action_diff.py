"""Observed local-player actions and contemporaneous stock changes.

This module consumes exported snapshots; it never accesses the game. A changed
state is evidence of a result, not proof of which mouse input caused it. In
particular, a vanished construction is not automatically a cancellation and a
stock fall is not an isolated cost. Intervals retain game and wall-clock bounds.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys

try:
    from .capture_identity import audited_identity, history_join_decisions, identity_mismatch, native_fingerprint
    from .player_observation import action_diff_observation
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from capture_identity import audited_identity, history_join_decisions, identity_mismatch, native_fingerprint
    from player_observation import action_diff_observation


GLOBAL_EVENTS = {'SetWinter', 'WorldEvent'}
UNIT_FIELDS = ('assigned_job', 'assign_to')
WORK_FIELDS = ('cur_job', 'target_zone_id', 'going_to', 'has_no_work')
LIMITS = [
    'Observed state changes are not a recording of every command; intermediate changes between samples can be missed.',
    'Resources are simultaneous net stock changes including production, consumption, spending and rewards; no isolated action cost or causal contribution is inferred.',
    'Construction disappearance alone is unresolved removal, not cancellation, destruction or completion.',
    'Native building completion can be matched by zone, kind and position; ambiguous matches remain unresolved.',
    'Repeated paused samples have no game-time duration; their ordering is given by sampling/wall-clock sequence.',
    'Session identity, time rollback or native-log reset begins a new segment; no differences cross segment boundaries.',
]


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _time(sample):
    value = float(sample['game_time_seconds'])
    if not math.isfinite(value) or value < 0:
        raise ValueError('Invalid game time')
    return value


def _entities(sample, field):
    rows = sample.get(field, [])
    result = {}
    for row in rows:
        uid = row.get('uid', row.get('entity_uid'))
        if uid is None:
            raise ValueError(f'{field}: entity without uid')
        if uid in result:
            raise ValueError(f'{field}: duplicate uid {uid}')
        result[uid] = row
    return result


def _session(sample):
    return (sample.get('session_key'), sample.get('capture_run_id'), sample.get('capture_segment_id'),
            sample.get('pid'), sample.get('clan'),
            sample.get('local_player_entity_uid'))


def _bind_native(events, context):
    """Bind only native logs read inside this exact snapshot/export context."""
    return [dict(event, **{key: context[key] for key in
                          ('session_key', 'capture_run_id', 'capture_segment_id', 'pid', 'local_player_entity_uid')
                          if context.get(key) is not None}) for event in events]


def _interval(before, after):
    start, end = _time(before), _time(after)
    return {'start_game_seconds': start, 'end_game_seconds': end,
            'start_exclusive': True, 'end_inclusive': True,
            'duration_game_seconds': end - start,
            'start_captured_at': before.get('captured_at'),
            'end_captured_at': after.get('captured_at'),
            'paused_or_same_game_time': abs(end - start) < 1e-7}


def _construction(row):
    return bool(row.get('is_construction', row.get('kind') == 'Construction'))


def _pos(row):
    value = row.get('position')
    if isinstance(value, dict):
        return value.get('x'), value.get('y')
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return value[0], value[1]
    return None


def _same_location(first, second):
    a, b = _pos(first), _pos(second)
    return a is None or b is None or a == b


def _native_building(ev):
    params = ev.get('params', [])
    if ev.get('kind') != 'AddBuilding' or len(params) < 3:
        return None
    zone = params[1] if isinstance(params[1], dict) else {}
    return {'zone_id': zone.get('zone_id'), 'kind': params[2],
            'position': params[3] if len(params) > 3 else None,
            'player_uid': params[0].get('entity_uid') if isinstance(params[0], dict) else None}


def _native_local(ev, before, after):
    if ev.get('kind') in GLOBAL_EVENTS:
        return True
    uid = after.get('local_player_entity_uid')
    params = ev.get('params', [])
    if uid is not None and any(isinstance(p, dict) and p.get('entity_uid') == uid for p in params):
        return True
    if ev.get('kind') == 'RemoveBuilding':
        zones = {b.get('zone_id') for s in (before, after) for b in s.get('buildings', [])}
        return any(isinstance(p, dict) and p.get('zone_id') in zones for p in params)
    return False


def _matches_target(intent, building):
    target = intent.get('target', {})
    if target.get('uid') is not None:
        return target['uid'] == building.get('uid')
    # A zone/kind alone is insufficient when two simultaneous constructions exist.
    return (target.get('zone_id') is not None and target.get('position') is not None
            and target['zone_id'] == building.get('zone_id')
            and _same_location(target, building)
            and target.get('kind') in (building.get('target_kind'), building.get('kind')))


def _matching_cancel(intents, building, start, end, context):
    matches = []
    for intent in intents:
        # A journal must identify the recording branch. A runtime session alone
        # can outlive an in-place rollback and is insufficient for commands.
        if not context.get('capture_segment_id') or not intent.get('capture_segment_id'):
            continue
        if identity_mismatch(context, intent, require_segment=True):
            continue
        if intent.get('kind') not in ('cancel_construction', 'cancel_building'):
            continue
        at = intent.get('game_time_seconds')
        if isinstance(at, (int, float)) and start - 1 <= at <= end + 1 and _matches_target(intent, building):
            matches.append(intent)
    return matches


def _stock_changes(before, after):
    old, new = before.get('stocks', before.get('resources', {})), after.get('stocks', after.get('resources', {}))
    dt = _time(after) - _time(before)
    result = {}
    for key in sorted(set(old) & set(new)):
        a, b = old[key], new[key]
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            continue
        if not math.isfinite(a) or not math.isfinite(b):
            continue
        result[key] = {'before': a, 'after': b, 'net_observed_change': b - a,
                       'observed_net_rate_per_game_second': (b - a) / dt if dt > 1e-7 else None}
    return result


def diff_snapshots(before, after, native_events=(), intents=()):
    """Return a single interval; require same session and nondecreasing time.

    native_events may include full history; only local events inside this interval
    (with one second tolerance for the native integer time) help disambiguation.
    UI journals describe issued or confirmed commands, and must name their target.
    """
    if (identity_mismatch(before, after, require_segment=True) or _session(before) != _session(after)
            or _time(after) < _time(before) - 1e-7):
        raise ValueError('Session changed; initialize a new baseline')
    if 'native_events' in before and 'native_events' in after:
        prefix, current = native_fingerprint(before['native_events']), native_fingerprint(after['native_events'])
        if prefix and (len(current) < len(prefix) or current[:len(prefix)] != prefix):
            raise ValueError('Native log changed; initialize a new baseline')
    interval = _interval(before, after)
    start, end = interval['start_game_seconds'], interval['end_game_seconds']
    stock = _stock_changes(before, after)
    # Bare externally supplied events are not assumed to belong to this match.
    native = [ev for ev in native_events if not identity_mismatch(after, ev, require_segment=True)
              and isinstance(ev.get('time'), (int, float))
              and start - 1 <= ev['time'] <= end + 1 and _native_local(ev, before, after)]
    events = []

    def emit(kind, entity=None, changes=None, confidence='high', evidence=(), **extra):
        event = {'kind': kind, 'source': 'state_diff', 'time_interval': deepcopy(interval),
                 'confidence': {'level': confidence, 'applies_to': 'observed state/transition, not cause or exact command time'},
                 'evidence': list(evidence) or [{'source': 'consecutive_local_player_snapshots'}],
                 'stock_change_reference': 'interval.resource_changes',
                 'attribution': 'contemporaneous_only'}
        if entity is not None:
            event['entity'] = deepcopy(entity)
        if changes is not None:
            event['changes'] = deepcopy(changes)
        event.update(extra)
        events.append(event)

    old_units, new_units = _entities(before, 'units'), _entities(after, 'units')
    for uid, row in new_units.items():
        previous = old_units.get(uid)
        if previous is None:
            emit('unit_first_observed', row)
            continue
        entity = {'uid': uid, 'kind': row.get('kind'), 'zone_id': row.get('zone_id')}
        if previous.get('kind') != row.get('kind'):
            emit('unit_kind_changed', entity, {'kind': {'before': previous.get('kind'), 'after': row.get('kind')}},
                 interpretation='Unit profession/type changed; input source is unknown.')
        for name, fields in [('unit_assignment_changed', UNIT_FIELDS), ('unit_work_target_changed', WORK_FIELDS)]:
            changes = {f: {'before': previous.get(f), 'after': row.get(f)} for f in fields
                       if _canonical(previous.get(f)) != _canonical(row.get(f))}
            if changes:
                emit(name, entity, changes,
                     interpretation='Assignment/working state changed; this can also follow automatic game logic.')
        if previous.get('zone_id') != row.get('zone_id'):
            emit('unit_zone_changed', entity, {'zone_id': {'before': previous.get('zone_id'), 'after': row.get('zone_id')}},
                 interpretation='Observed in another zone; this is not evidence of a profession change.')
    for uid, row in old_units.items():
        if uid not in new_units:
            emit('unit_no_longer_observed', row,
                 interpretation='Reason unknown: death, transfer or scope/state changes require separate evidence.')

    old_buildings, new_buildings = _entities(before, 'buildings'), _entities(after, 'buildings')
    consumed_new = set()
    consumed_native = set()

    def possible_predecessors(result):
        return [b for b in old_buildings.values() if _construction(b)
                and b.get('target_kind') == result.get('kind')
                and b.get('zone_id') == result.get('zone_id') and _same_location(b, result)]

    for uid, previous in old_buildings.items():
        row = new_buildings.get(uid)
        if _construction(previous) and (row is None or not _construction(row)):
            target = previous.get('target_kind')
            # Same uid transformation is strongest. Otherwise only one new entity
            # at the same zone, target kind and (when available) location can match.
            candidates = [b for key, b in new_buildings.items()
                          if not _construction(b) and key not in old_buildings and key not in consumed_new
                          and b.get('zone_id') == previous.get('zone_id')
                          and target is not None and b.get('kind') == target
                          and _same_location(previous, b) and len(possible_predecessors(b)) == 1]
            if row is not None and not _construction(row):
                candidates = [row]
            completions = []
            for native_offset, ev in enumerate(native):
                info = _native_building(ev)
                if (info and target is not None and info['kind'] == target
                        and info['zone_id'] == previous.get('zone_id')
                        and _same_location(previous, info)
                        and native_offset not in consumed_native and len(possible_predecessors(info)) == 1):
                    completions.append((native_offset, ev))
            if len(candidates) == 1 or len(completions) == 1:
                evidence = [{'source': 'construction_state_transition', 'construction_uid': uid}]
                if len(candidates) == 1:
                    consumed_new.add(candidates[0]['uid'])
                    evidence.append({'source': 'completed_building_entity', 'uid': candidates[0]['uid']})
                evidence += [{'source': 'native_GameState.logs', 'index': e.get('index'),
                              'kind': e['kind'], 'time': e['time']} for _, e in completions]
                consumed_native.update(offset for offset, _ in completions)
                emit('construction_completed', previous, confidence='high' if completions or row else 'medium', evidence=evidence,
                     resulting_building=candidates[0] if len(candidates) == 1 else None)
            else:
                cancellations = _matching_cancel(intents, previous, start, end, after)
                confirmed = [i for i in cancellations if i.get('status') == 'confirmed']
                if confirmed:
                    emit('construction_cancelled', previous, confidence='high',
                         evidence=[{'source': 'construction_disappearance'},
                                   {'source': 'intent_journal', 'entries': confirmed}],
                         interpretation='Targeted confirmed cancellation and disappearance agree; resources remain net observations.')
                elif cancellations:
                    emit('construction_removed_after_cancel_command', previous, confidence='medium',
                         evidence=[{'source': 'construction_disappearance'},
                                   {'source': 'intent_journal', 'entries': cancellations}],
                         interpretation='Targeted cancel command preceded disappearance; command success is not independently confirmed.')
                else:
                    emit('construction_removed_unresolved', previous, confidence='high',
                         interpretation='Disappearance observed; completion, cancellation, destruction or scope change unresolved.')
            continue
        if row is None:
            emit('building_no_longer_observed', previous,
                 interpretation='Removal observed; reason is unresolved.')
            continue
        if _construction(row):
            a, b = previous.get('construction_progress'), row.get('construction_progress')
            if a is not None and b is not None and a != b:
                emit('construction_progress_changed', {'uid': uid, 'target_kind': row.get('target_kind'), 'zone_id': row.get('zone_id')},
                     {'construction_progress': {'before': a, 'after': b}})
        for field, kind in [('upgraded', 'building_upgrade_state_changed'), ('assigned_unit_ids', 'building_worker_assignment_changed')]:
            a, b = previous.get(field), row.get(field)
            if field == 'assigned_unit_ids' and isinstance(a, list) and isinstance(b, list):
                a, b = sorted(a), sorted(b)
            if a != b:
                emit(kind, {'uid': uid, 'kind': row.get('kind'), 'zone_id': row.get('zone_id')},
                     {field: {'before': a, 'after': b}})
    for uid, row in new_buildings.items():
        if uid in old_buildings or uid in consumed_new:
            continue
        emit('construction_started_observed' if _construction(row) else 'building_first_observed', row,
             interpretation='Construction/entity first observed within interval; exact command time and cost are unknown.')
    for field in ('population', 'population_cap', 'in_winter', 'session_game_finished'):
        if field in before and field in after and before[field] != after[field]:
            emit(field + '_changed', changes={field: {'before': before[field], 'after': after[field]}})
    for field, event_kind in [('discovered_zone_ids', 'zone_discovered_observed'), ('owned_techs', 'tech_obtained_observed')]:
        if field in before and field in after:
            for value in sorted(set(after[field]) - set(before[field])):
                emit(event_kind, changes={field: {'added': value}})
    return {'time_interval': interval, 'resource_changes': stock, 'events': events,
            'native_events_near_interval': native,
            'sample_quality': {'atomic': False,
                               'before_span_game_seconds': before.get('sample_span_game_seconds'),
                               'after_span_game_seconds': after.get('sample_span_game_seconds'),
                               'broad_interval': end - start > 5},
            'attribution': 'temporal_association_only'}


class ActionDiffer:
    """Incremental differ with explicit baseline/reset records."""
    def __init__(self):
        self.previous = None
        self.native_prefix = []
        self.segment = 0
        self.sample_index = -1
        self.skipped_since_valid = 0

    def feed(self, snapshot, native_events=None, intents=()):
        self.sample_index += 1
        if snapshot.get('status', 'ok') != 'ok':
            self.skipped_since_valid += 1
            return {'record_kind': 'sample_rejected', 'sample_index': self.sample_index,
                    'status': snapshot.get('status'), 'error': snapshot.get('error')}
        if snapshot.get('observation_type') == 'PlayerObservation':
            if native_events is not None:
                raise ValueError('External native events are not accepted for PlayerObservation')
            snapshot = action_diff_observation(snapshot)
        _time(snapshot)
        _entities(snapshot, 'units')
        _entities(snapshot, 'buildings')
        if not snapshot.get('session_key'):
            self.skipped_since_valid += 1
            return {'record_kind': 'sample_rejected', 'sample_index': self.sample_index,
                    'status': 'missing_session_identity', 'error': 'Unidentified legacy snapshots require audited provenance'}
        supplied = list(native_events) if native_events is not None else None
        native = (_bind_native(snapshot.get('native_events', ()), snapshot)
                  if supplied is None else [event for event in supplied
                                           if not identity_mismatch(snapshot, event, require_segment=True)])
        prefix = native_fingerprint(native)
        reason = None
        if self.previous is None:
            reason = 'initial_baseline'
        elif _session(self.previous) != _session(snapshot):
            reason = 'session_identity_changed'
        elif _time(snapshot) < _time(self.previous) - 1e-7:
            reason = 'game_time_rollback'
        elif self.native_prefix and (len(prefix) < len(self.native_prefix)
                                    or prefix[:len(self.native_prefix)] != self.native_prefix):
            reason = 'native_log_replaced_or_truncated'
        if reason:
            self.segment += 1
            result = {'record_kind': 'baseline', 'reason': reason, 'game_time_seconds': _time(snapshot),
                      'session_key': snapshot.get('session_key'),
                      'capture_run_id': snapshot.get('capture_run_id'),
                      'capture_segment_id': snapshot.get('capture_segment_id'),
                      'native_events_already_present': len(native),
                      'unit_count': len(snapshot.get('units', [])), 'building_count': len(snapshot.get('buildings', []))}
        else:
            result = diff_snapshots(self.previous, snapshot, native, intents)
            result['record_kind'] = 'interval'
            # The near-interval list is for association; this list is append-only
            # and never repeats the same native log item in a segment.
            result['new_native_events'] = [ev for ev in native[len(self.native_prefix):]
                                           if _native_local(ev, self.previous, snapshot)]
            result['skipped_samples_since_previous_valid'] = self.skipped_since_valid
            for i, event in enumerate(result['events']):
                event['event_id'] = f's{self.segment}:n{self.sample_index}:e{i}'
        result.update(segment=self.segment, sample_index=self.sample_index,
                      rejected_external_native_event_count=0 if supplied is None else len(supplied) - len(native))
        self.previous = deepcopy(snapshot)
        self.native_prefix = prefix
        self.skipped_since_valid = 0
        return result


def make_action_review(samples, history=None, intents=(), *, audited_provenance=None):
    """Preserve input order: sorting would hide a save-load/time rollback."""
    samples = [audited_identity(sample, audited_provenance) for sample in samples]
    intents = [audited_identity(intent, audited_provenance) for intent in intents]
    differ, records, accepted = ActionDiffer(), [], []
    history_context = audited_identity(history, audited_provenance) if history is not None else None
    allowed, history_join = (history_join_decisions(history_context, samples, audited_provenance)
                            if history_context is not None else (set(), None))
    history_events = (history_context or {}).get('events', [])
    for index, sample in enumerate(samples):
        # External history needs an explicit compatible runtime identity and an
        # unambiguous recording branch. Missing IDs never match by clan/time.
        external = None
        if 'native_events' not in sample and index in allowed:
            external = _bind_native([ev for ev in history_events
                                     if ev.get('time', math.inf) <= sample.get('game_time_seconds', -1) + 1], sample)
        record = differ.feed(sample, native_events=external, intents=intents)
        records.append(record)
        if record['record_kind'] != 'sample_rejected':
            accepted.append(sample)
    events = [ev for row in records for ev in row.get('events', [])]
    interval_rows = [row for row in records if row['record_kind'] == 'interval']
    gaps = [{'segment': row['segment'], 'sample_index': row['sample_index'],
             'time_interval': row['time_interval'],
             'skipped_samples_since_previous_valid': row['skipped_samples_since_previous_valid']}
            for row in interval_rows if row['sample_quality']['broad_interval']
            or row['skipped_samples_since_previous_valid']]
    return {'schema_version': 2, 'source': 'offline_local_player_state_diff',
            'external_history_join': history_join, 'audited_provenance': audited_provenance,
            'input_sample_count': len(records), 'valid_sample_count': len(accepted),
            'segment_count': differ.segment, 'event_count': len(events),
            'event_counts': dict(Counter(ev['kind'] for ev in events)),
            'game_finished': accepted[-1].get('session_game_finished') if accepted else None,
            'coverage': {'rejected_sample_count': len(records) - len(accepted),
                         'rejected_status_counts': dict(Counter(row.get('status') for row in records if row['record_kind'] == 'sample_rejected')),
                         'largest_interval_game_seconds': max((row['time_interval']['duration_game_seconds'] for row in interval_rows), default=0),
                         'broad_or_failed_sample_intervals': gaps},
            'records': records, 'intent_journal': list(intents), 'limits': LIMITS}


def _read_rows(path):
    text = path.read_text(encoding='utf-8-sig')
    if path.suffix.lower() == '.ndjson' or path.suffix.lower() == '.jsonl':
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    data = json.loads(text)
    return data if isinstance(data, list) else data.get('entries', [data])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('samples', type=Path)
    parser.add_argument('--history', type=Path)
    parser.add_argument('--intents', type=Path)
    parser.add_argument('--audited-provenance', type=Path,
                        help='Named evidence-backed legacy identity/segment migration JSON')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    samples = _read_rows(args.samples)
    history = json.loads(args.history.read_text(encoding='utf-8')) if args.history else None
    intents = _read_rows(args.intents) if args.intents else []
    audited = json.loads(args.audited_provenance.read_text(encoding='utf-8')) if args.audited_provenance else None
    review = make_action_review(samples, history, intents, audited_provenance=audited)
    sources = [args.samples, *([args.history] if args.history else []), *([args.intents] if args.intents else [])]
    review['provenance'] = [{'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for path in sources]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: review[k] for k in ('valid_sample_count', 'segment_count', 'event_count', 'event_counts', 'game_finished')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
