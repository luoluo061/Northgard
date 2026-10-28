"""Summarize a stopped local capture without game access or production estimates.

Only player-observations.ndjson and health.ndjson are read. Rows stay in source
order within strict runtime-session/run/segment groups. A game-time gap is a
sampling observation, not proof of missed game events or resource production.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys

MAX_LINE_BYTES = 2 * 1024 * 1024
MAX_DIAGNOSTIC_EXAMPLES = 100
MAX_INTERVAL_EXAMPLES = 100
IDENTITY_FIELDS = ('session_key', 'capture_run_id', 'capture_segment_id')
RESOURCES = ('Food', 'Wood', 'Money', 'Stone', 'Iron', 'Lore')
COMPONENT_PATHS = {
    'economy': ('economy', 'status'),
    'operations': ('operations', 'status'),
    'map_observation': ('map_observation', 'status'),
    'labor': ('labor', 'status'),
    'population_growth': ('population_growth', 'status'),
    'match_context': ('match_context', 'status'),
    'base_neighborhood': ('base_neighborhood', 'status'),
    'known_zone_details': ('known_zone_details', 'status'),
    'feast_quote': ('operations', 'feast_quote', 'status'),
}
END_STATE_FIELDS = ('session_game_finished', 'local_player_game_over', 'local_player_has_conceded')
EVENT_FIELDS = ('index', 'time', 'kind', 'actor', 'source', 'zone_id',
                'native_flag', 'building_kind', 'tech_id', 'unit_or_upgrade_kind')


class SummaryError(ValueError):
    """The source is not a stable stopped capture, or the request is invalid."""


class NonFiniteJSON(ValueError):
    pass


class Diagnostics:
    """Bound examples while retaining exact aggregate counts for large captures."""
    def __init__(self):
        self.counts = Counter()
        self.fields = Counter()
        self.examples = []

    def add(self, code, file, line=None, field=None):
        self.counts[code] += 1
        if field:
            self.fields[f'{code}:{field}'] += 1
        if len(self.examples) < MAX_DIAGNOSTIC_EXAMPLES:
            item = {'code': code, 'file': file}
            if line is not None:
                item['line'] = line
            if field:
                item['field'] = field
            self.examples.append(item)

    def result(self):
        total = sum(self.counts.values())
        return {'total': total, 'counts': dict(self.counts),
                'field_counts': dict(self.fields), 'examples': self.examples,
                'examples_omitted': total - len(self.examples)}


def _number(value):
    return type(value) in (int, float) and abs(value) < 2**53 and math.isfinite(value)


def _clock(value):
    return _number(value) and value >= 0


def _strict_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise NonFiniteJSON('Non-finite JSON number')
    return value


def _constant(_):
    raise NonFiniteJSON('Non-finite JSON number')


def _loads(raw):
    return json.loads(raw, parse_float=_strict_float, parse_constant=_constant)


def _signature(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


def _read_manifest(path):
    try:
        with path.open('rb') as handle:
            raw = handle.read(MAX_LINE_BYTES + 1)
        if len(raw) > MAX_LINE_BYTES:
            raise SummaryError('manifest.json exceeds the 2 MiB limit')
        value = _loads(raw)
    except (OSError, ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, SummaryError):
            raise
        raise SummaryError('manifest.json is missing or invalid') from error
    if type(value) is not dict or value.get('recording_state') != 'stopped':
        raise SummaryError('Only a capture with manifest recording_state=stopped can be summarized')
    return value


def _rows(path, diagnostics, stats, on_rejected=None):
    """Read bounded physical lines, drain oversize lines, then resume parsing."""
    item = {'physical_lines': 0, 'object_rows': 0, 'rejected_lines': 0,
            'size_bytes': None, 'present': path.is_file()}
    stats[path.name] = item
    if not path.is_file():
        diagnostics.add('missing_file', path.name)
        return
    def rejected(code, line):
        item['rejected_lines'] += 1
        if on_rejected is not None:
            on_rejected(code, line)

    before = _signature(path)
    item['size_bytes'] = before[0]
    with path.open('rb') as handle:
        while True:
            raw = handle.readline(MAX_LINE_BYTES + 1)
            if not raw:
                break
            item['physical_lines'] += 1
            line = item['physical_lines']
            if len(raw) > MAX_LINE_BYTES:
                terminated = raw.endswith(b'\n')
                while not terminated:
                    tail = handle.readline(MAX_LINE_BYTES + 1)
                    if not tail:
                        break
                    terminated = tail.endswith(b'\n')
                diagnostics.add('line_too_large', path.name, line)
                if not terminated:
                    diagnostics.add('incomplete_tail', path.name, line)
                rejected('line_too_large', line)
                continue
            terminated = raw.endswith(b'\n')
            if not raw.strip():
                diagnostics.add('blank_line', path.name, line)
                rejected('blank_line', line)
                continue
            if not terminated:
                diagnostics.add('unterminated_tail', path.name, line)
            try:
                row = _loads(raw)
            except NonFiniteJSON:
                diagnostics.add('non_finite_json', path.name, line)
                rejected('non_finite_json', line)
                continue
            except (ValueError, UnicodeError, RecursionError):
                diagnostics.add('invalid_json', path.name, line)
                if not terminated:
                    diagnostics.add('incomplete_tail', path.name, line)
                rejected('invalid_json', line)
                continue
            if type(row) is not dict:
                diagnostics.add('non_object_row', path.name, line)
                rejected('non_object_row', line)
                continue
            item['object_rows'] += 1
            yield line, row
    if _signature(path) != before:
        raise SummaryError(f'{path.name} changed while being read; stop the writer first')


def _identity(row):
    if all(type(row.get(key)) is str and bool(row[key].strip()) for key in IDENTITY_FIELDS):
        return tuple(row[key] for key in IDENTITY_FIELDS)
    return None


def _status(value):
    if value is None:
        return 'missing'
    return value if type(value) is str and value else 'invalid'


def _component_statuses(row):
    result = {}
    for name, path in COMPONENT_PATHS.items():
        value = row
        for key in path:
            if type(value) is not dict:
                value = None
                break
            value = value.get(key)
        result[name] = _status(value)
    return result


def _wall_clock(row, diagnostics, filename, line):
    value = row.get('captured_at')
    if value is None:
        diagnostics.add('missing_or_null_field', filename, line, 'captured_at')
        return None
    if type(value) is not str:
        diagnostics.add('invalid_field', filename, line, 'captured_at')
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        diagnostics.add('invalid_field', filename, line, 'captured_at')
        return None
    if parsed.tzinfo is None:
        diagnostics.add('wall_clock_timezone_missing', filename, line, 'captured_at')
    return value


def _numeric_field(row, key, diagnostics, line, *, nonnegative=False):
    value = row.get(key)
    if value is None:
        diagnostics.add('missing_or_null_field', 'player-observations.ndjson', line, key)
        return None
    if not _number(value) or nonnegative and value < 0:
        diagnostics.add('invalid_field', 'player-observations.ndjson', line, key)
        return None
    return value


def _job_label(value):
    if type(value) is dict:
        value = value.get('kind')
    return value if type(value) is str and value else None


def _snapshot(row, when, line, diagnostics):
    stocks = row.get('stocks')
    if type(stocks) is not dict:
        diagnostics.add('missing_or_invalid_field', 'player-observations.ndjson', line, 'stocks')
        stocks = {}
    stock_values = {}
    for resource in RESOURCES:
        value = stocks.get(resource)
        if value is None:
            diagnostics.add('missing_or_null_field', 'player-observations.ndjson', line, f'stocks.{resource}')
            stock_values[resource] = None
        elif not _number(value):
            diagnostics.add('invalid_field', 'player-observations.ndjson', line, f'stocks.{resource}')
            stock_values[resource] = None
        else:
            stock_values[resource] = value
    snapshot = {'source_line': line, 'game_time_seconds': when,
                'captured_at': _wall_clock(row, diagnostics, 'player-observations.ndjson', line),
                'stocks': stock_values,
                'population': _numeric_field(row, 'population', diagnostics, line, nonnegative=True),
                'population_cap': _numeric_field(row, 'population_cap', diagnostics, line, nonnegative=True),
                'component_statuses': _component_statuses(row)}
    for field in END_STATE_FIELDS:
        snapshot[field] = row.get(field) if type(row.get(field)) is bool else None
    if type(row.get('sequence')) is int and row['sequence'] >= 0:
        snapshot['sequence'] = row['sequence']
    units = row.get('units')
    if type(units) is not list:
        diagnostics.add('missing_or_invalid_field', 'player-observations.ndjson', line, 'units')
        snapshot.update(unit_record_count=None, unit_kind_counts=None,
                        assigned_job_kind_counts=None, current_job_kind_counts=None,
                        unknown_unit_kind_count=None, unknown_assigned_job_count=None,
                        unknown_current_job_count=None)
        return snapshot
    kinds, assigned, current = Counter(), Counter(), Counter()
    unknown_kind = unknown_assigned = unknown_current = 0
    for unit in units:
        if type(unit) is not dict:
            diagnostics.add('invalid_unit_row', 'player-observations.ndjson', line, 'units')
            unknown_kind += 1
            unknown_assigned += 1
            unknown_current += 1
            continue
        kind = _job_label(unit.get('kind'))
        if kind is None:
            diagnostics.add('missing_or_invalid_field', 'player-observations.ndjson', line, 'units.kind')
            unknown_kind += 1
        else:
            kinds[kind] += 1
        job = _job_label(unit.get('assigned_job'))
        if job is None:
            unknown_assigned += 1
        else:
            assigned[job] += 1
        job = _job_label(unit.get('cur_job'))
        if job is None:
            unknown_current += 1
        else:
            current[job] += 1
    snapshot.update(unit_record_count=len(units), unit_kind_counts=dict(kinds),
                    assigned_job_kind_counts=dict(assigned), current_job_kind_counts=dict(current),
                    unknown_unit_kind_count=unknown_kind, unknown_assigned_job_count=unknown_assigned,
                    unknown_current_job_count=unknown_current)
    return snapshot


def _calendar_bucket(when):
    month_index = math.floor(when / 60)
    within_month = when - month_index * 60
    third = min(2, math.floor(within_month / 20))
    calendar_index = month_index + 2  # Zero is A800 March, not January.
    year, month = 800 + calendar_index // 12, calendar_index % 12 + 1
    return (year, month, third), {
        'year': year, 'month': month, 'third': ('early', 'middle', 'late')[third],
        'label': f'A{year}-{month:02d}-' + ('初', '中', '末')[third],
        'start_game_time_seconds': month_index * 60 + third * 20,
        'end_game_time_seconds_exclusive': month_index * 60 + (third + 1) * 20,
    }


class Group:
    def __init__(self, identity, gap_threshold):
        self.identity = dict(zip(IDENTITY_FIELDS, identity))
        self.threshold = gap_threshold
        self.rows = 0
        self.state_rows = 0
        self.contiguous_blocks = 0
        self.interruptions = 0
        self.end_states = {name: Counter() for name in END_STATE_FIELDS}
        self.statuses = Counter()
        self.components = {name: Counter() for name in COMPONENT_PATHS}
        self.times = set()
        self.first_time = self.last_time = self.min_time = self.max_time = None
        self.previous = None
        self.stagnant = self.rollbacks = self.gap_count = 0
        self.max_gap = None
        self.gaps = []
        self.rollback_examples = []
        self.first = self.last = None
        self.buckets = {}
        self.events = {}
        self.event_seen = self.event_duplicates = 0

    def feed(self, row, line, diagnostics):
        self.rows += 1
        for name in END_STATE_FIELDS:
            value = row.get(name)
            state = str(value).lower() if type(value) is bool else 'missing_or_invalid'
            self.end_states[name][state] += 1
        self.statuses[_status(row.get('status'))] += 1
        for name, status in _component_statuses(row).items():
            self.components[name][status] += 1
        when = row.get('game_time_seconds')
        if not _clock(when):
            diagnostics.add('missing_or_invalid_field', 'player-observations.ndjson', line, 'game_time_seconds')
            self.previous = None
            return
        if row.get('status') != 'ok':
            diagnostics.add('observation_not_ok', 'player-observations.ndjson', line, 'status')
            self.previous = None
            return
        self.state_rows += 1
        self.times.add(when)
        if self.first_time is None:
            self.first_time = when
        self.last_time = when
        self.min_time = when if self.min_time is None else min(self.min_time, when)
        self.max_time = when if self.max_time is None else max(self.max_time, when)
        if self.previous is not None:
            prior_time, prior_line = self.previous
            delta = when - prior_time
            interval = {'from_source_line': prior_line, 'to_source_line': line,
                        'from_game_time_seconds': prior_time, 'to_game_time_seconds': when,
                        'delta_game_seconds': delta}
            if delta == 0:
                self.stagnant += 1
            elif delta < 0:
                self.rollbacks += 1
                if len(self.rollback_examples) < MAX_INTERVAL_EXAMPLES:
                    self.rollback_examples.append(interval)
                diagnostics.add('game_clock_rollback_within_identity', 'player-observations.ndjson', line)
            else:
                self.max_gap = delta if self.max_gap is None else max(self.max_gap, delta)
                if delta > self.threshold:
                    self.gap_count += 1
                    if len(self.gaps) < MAX_INTERVAL_EXAMPLES:
                        self.gaps.append(interval)
        self.previous = when, line
        state = _snapshot(row, when, line, diagnostics)
        if self.first is None:
            self.first = state
        self.last = state
        key, calendar = _calendar_bucket(when)
        bucket = self.buckets.setdefault(key, {**calendar, 'observation_rows': 0,
                                               'first_state': state, 'last_state': state})
        bucket['observation_rows'] += 1
        bucket['last_state'] = state
        self._events(row, when, line, diagnostics)

    def _events(self, row, when, line, diagnostics):
        events = row.get('own_events')
        if type(events) is not list:
            diagnostics.add('missing_or_invalid_field', 'player-observations.ndjson', line, 'own_events')
            return
        for event in events:
            if (type(event) is not dict or type(event.get('index')) is not int or event['index'] < 0
                    or not _clock(event.get('time')) or event['time'] > when + 1
                    or type(event.get('kind')) is not str or not event['kind']
                    or event.get('actor') != 'self' or event.get('source') != 'native_own_success_event'):
                diagnostics.add('invalid_or_unverified_own_event', 'player-observations.ndjson', line, 'own_events')
                continue
            content = {key: event[key] for key in EVENT_FIELDS if key in event}
            canonical = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            fingerprint = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
            variants = self.events.setdefault(event['index'], {})
            self.event_seen += 1
            if fingerprint in variants:
                self.event_duplicates += 1
                entry = variants[fingerprint]
                entry['observations'] += 1
                entry['last_observed_source_line'] = line
                entry['last_observed_game_time_seconds'] = when
            else:
                if variants:
                    diagnostics.add('native_event_index_content_conflict', 'player-observations.ndjson', line, 'own_events')
                variants[fingerprint] = {
                    'event': content, 'content_sha256': fingerprint, 'observations': 1,
                    'first_observed_source_line': line, 'last_observed_source_line': line,
                    'first_observed_game_time_seconds': when, 'last_observed_game_time_seconds': when,
                }

    def result(self):
        events, conflicts = [], []
        for index, variants in self.events.items():
            events.extend(variants.values())
            if len(variants) > 1:
                conflicts.append({'index': index, 'variant_count': len(variants),
                                  'content_sha256': list(variants)})
        return {**self.identity, 'observation_rows': self.rows, 'usable_state_rows': self.state_rows,
                'status_counts': dict(self.statuses),
                'source_contiguous_blocks': self.contiguous_blocks,
                'source_interruption_count': self.interruptions,
                'end_state_observation_counts': {name: {state: counts[state] for state in ('true', 'false', 'missing_or_invalid')}
                                                 for name, counts in self.end_states.items()},
                'component_status_counts': {key: dict(value) for key, value in self.components.items()},
                'clock': {'first_game_time_seconds': self.first_time, 'last_game_time_seconds': self.last_time,
                          'minimum_game_time_seconds': self.min_time, 'maximum_game_time_seconds': self.max_time,
                          'unique_game_times': len(self.times), 'stagnant_clock_observations': self.stagnant,
                          'maximum_positive_interval_game_seconds': self.max_gap,
                          'rollback_count': self.rollbacks, 'rollback_examples': self.rollback_examples,
                          'rollback_examples_omitted': self.rollbacks - len(self.rollback_examples),
                          'suspicious_sampling_interval_count': self.gap_count,
                          'suspicious_sampling_intervals': self.gaps,
                          'suspicious_interval_examples_omitted': self.gap_count - len(self.gaps)},
                'first_state': self.first, 'last_state': self.last,
                'calendar_buckets': list(self.buckets.values()),
                'native_events': {'source': 'own_events_only', 'observations': self.event_seen,
                                  'repeated_observations_deduplicated': self.event_duplicates,
                                  'unique_index_count': len(self.events), 'unique_content_count': len(events),
                                  'index_conflicts': conflicts, 'events': events}}


def _health(path, diagnostics, stats):
    statuses, reasons, pairs = Counter(), Counter(), Counter()
    components = {}
    count = with_identity = 0
    first_wall = last_wall = None
    for line, row in _rows(path, diagnostics, stats):
        count += 1
        status = _status(row.get('status'))
        statuses[status] += 1
        if status in ('missing', 'invalid'):
            diagnostics.add('missing_or_invalid_field', path.name, line, 'status')
        reason = row.get('reason_code')
        if reason is None:
            reason = 'not_reported'
        elif type(reason) is not str or not reason:
            diagnostics.add('invalid_field', path.name, line, 'reason_code')
            reason = 'invalid'
        reasons[reason] += 1
        pairs[(status, reason)] += 1
        wall = _wall_clock(row, diagnostics, path.name, line)
        if count == 1:
            first_wall = wall
        last_wall = wall
        with_identity += int(_identity(row) is not None)
        for name, value in row.items():
            if name.endswith('_status') and name != 'status':
                components.setdefault(name, Counter())[_status(value)] += 1
    return {'rows': count, 'status_counts': dict(statuses), 'reason_code_counts': dict(reasons),
            'status_reason_counts': [{'status': status, 'reason_code': reason, 'count': number}
                                     for (status, reason), number in pairs.items()],
            'first_captured_at': first_wall, 'last_captured_at': last_wall,
            'rows_with_complete_identity': with_identity, 'rows_without_complete_identity': count - with_identity,
            'component_status_counts': {key: dict(value) for key, value in components.items()},
            'association_policy': 'No health row is assigned to a game by wall clock, sequence, or nearby game time. '
                                  'Non-match statuses and read errors do not automatically count as in-match gaps.'}


def summarize_capture(capture, *, gap_threshold=1.0):
    if not _number(gap_threshold) or gap_threshold <= 0:
        raise SummaryError('gap_threshold must be a finite positive number')
    capture = Path(capture).resolve()
    if not capture.is_dir():
        raise SummaryError('Capture must be an existing directory')
    manifest_path = capture / 'manifest.json'
    try:
        manifest_signature = _signature(manifest_path)
    except OSError as error:
        raise SummaryError('manifest.json is missing or unreadable') from error
    manifest = _read_manifest(manifest_path)
    diagnostics, stats, groups = Diagnostics(), {}, {}
    ungrouped = 0
    active_identity = None

    def interrupt(reason, line):
        nonlocal active_identity
        if active_identity is not None:
            groups[active_identity].previous = None
            groups[active_identity].interruptions += 1
        active_identity = None
        diagnostics.add('observation_stream_interrupted', 'player-observations.ndjson', line, reason)

    for line, row in _rows(capture / 'player-observations.ndjson', diagnostics, stats, interrupt):
        identity = _identity(row)
        if identity is None:
            ungrouped += 1
            interrupt('missing_identity', line)
            for key in IDENTITY_FIELDS:
                if type(row.get(key)) is not str or not row[key].strip():
                    diagnostics.add('missing_or_invalid_identity', 'player-observations.ndjson', line, key)
            continue
        if active_identity is not None and identity != active_identity:
            interrupt('identity_changed', line)
        if identity not in groups:
            groups[identity] = Group(identity, gap_threshold)
        group = groups[identity]
        if active_identity != identity:
            if group.rows:
                diagnostics.add('observation_group_reentered', 'player-observations.ndjson', line)
            group.previous = None
            group.contiguous_blocks += 1
        active_identity = identity
        group.feed(row, line, diagnostics)
        if row.get('status') != 'ok' or not _clock(row.get('game_time_seconds')):
            interrupt('non_ok_or_invalid_clock', line)
    health = _health(capture / 'health.ndjson', diagnostics, stats)
    if _signature(manifest_path) != manifest_signature:
        raise SummaryError('manifest.json changed while being read; stop the writer first')
    stop_reason = manifest.get('stop_reason')
    if stop_reason is None:
        diagnostics.add('missing_or_null_field', 'manifest.json', field='stop_reason')
    elif type(stop_reason) is not str or not stop_reason:
        diagnostics.add('invalid_field', 'manifest.json', field='stop_reason')
        stop_reason = None
    details = diagnostics.result()
    return {'schema_version': 1, 'summary_type': 'stopped_local_capture_summary',
            'status': 'completed_with_diagnostics' if details['total'] else 'ok',
            'recording_state': manifest['recording_state'], 'stop_reason': stop_reason,
            'collector_version': manifest.get('collector_version'),
            'source_files': stats, 'ungrouped_observation_rows': ungrouped,
            'grouping': list(IDENTITY_FIELDS), 'group_order': 'first appearance in source file; no cross-group stitching',
            'gap_threshold_game_seconds': gap_threshold,
            'calendar': {'game_time_zero': 'A800 March', 'game_seconds_per_month': 60,
                         'game_seconds_per_year': 720, 'month_thirds_game_seconds': 20},
            'groups': [group.result() for group in groups.values()], 'health': health,
            'diagnostics': details,
            'limits': ['This is a local sampling summary, not a complete command log or full-match replay.',
                       'Source row order and same-time state changes are retained in bucket endpoints; intermediate states stay in the original capture.',
                       'First/last states are observations, not values filled across missing fields or capture boundaries.',
                       'Game-time intervals are compared only inside uninterrupted source blocks of exact session/run/segment groups; no resources or labor are integrated.',
                       'Bad records, missing identity, non-ok states, invalid clocks and identity changes break adjacent-interval baselines; reentries are explicit.',
                       'Stop reason and native end-state booleans are observations, not inferred victory, defeat, or full-match completeness.',
                       'A stagnant game clock adds no simulated time; it can reflect pause or repeated sampling, not a verified pause cause.',
                       'Intervals above the threshold are suspicious sampling intervals, not proof every intervening event or sample is missing.',
                       'Native events can predate the first observation in their group; no later session supplies earlier missing events.',
                       'Job labels and unit kinds are observations, not measured per-unit production. No outcomes or advice are inferred.',
                       'Diagnostic and interval examples are bounded; aggregate counts are complete.']}


def write_summary(summary, output):
    """Exclusive creation never overwrites a capture or an earlier summary."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8', newline='\n') as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')


def summary_index(summary):
    return {'status': summary['status'], 'recording_state': summary['recording_state'],
            'stop_reason': summary['stop_reason'],
            'groups': [{**{key: group[key] for key in IDENTITY_FIELDS},
                        'observation_rows': group['observation_rows'],
                        'first_game_time_seconds': group['clock']['first_game_time_seconds'],
                        'last_game_time_seconds': group['clock']['last_game_time_seconds'],
                        'unique_game_times': group['clock']['unique_game_times'],
                        'suspicious_sampling_interval_count': group['clock']['suspicious_sampling_interval_count']}
                       for group in summary['groups']],
            'health_status_counts': summary['health']['status_counts'],
            'diagnostic_counts': summary['diagnostics']['counts']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True, help='Stopped local capture directory')
    parser.add_argument('--out', type=Path, help='New local summary JSON; existing files are never overwritten')
    parser.add_argument('--gap-threshold', type=float, default=1.0,
                        help='Flag positive sample intervals above this many game seconds (default: 1)')
    args = parser.parse_args(argv)
    try:
        summary = summarize_capture(args.capture, gap_threshold=args.gap_threshold)
        if args.out:
            write_summary(summary, args.out)
    except (SummaryError, OSError) as error:
        print(json.dumps({'status': 'error', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(summary_index(summary), ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
