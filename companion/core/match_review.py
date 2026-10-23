"""Join native events to observed match curves without claiming causation.

Native history and optional local-player state samples are offline inputs.
Stock differences are net observed changes, never inferred production or cost.
"""
import argparse
import glob
import json
from pathlib import Path
import sys

try:
    from .capture_identity import audited_identity, history_join_decisions
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from capture_identity import audited_identity, history_join_decisions

METRICS = ('food', 'wood', 'money', 'lore', 'population', 'happy', 'zones')
GLOBAL_EVENTS = {'SetWinter', 'WorldEvent'}


def curve_value(points, at_time, coverage_end):
    if at_time < 0 or at_time > coverage_end:
        return None
    eligible = [p for p in points if p['time'] <= at_time]
    if not eligible:
        return None
    point = eligible[-1]
    return {'value': point['value'], 'native_change_anchor_time': point['time']}


def make_review(history, samples=(), window=30, *, audited_provenance=None):
    if not 0 < window <= 3600:
        raise ValueError('Invalid comparison window')
    samples = list(samples)
    allowed, identity_join = history_join_decisions(history, samples, audited_provenance)
    history = audited_identity(history, audited_provenance)
    local = next(p for p in history['players'] if p.get('local_player'))
    uid = history['local_player_entity_uid']
    end = min(history['game_time_seconds'], history['logger_last_time'])
    def relevant(event):
        return event['kind'] in GLOBAL_EVENTS or any(
            isinstance(x, dict) and x.get('entity_uid') == uid for x in event['params'])
    events = [e for e in history['events'] if relevant(e)]
    clean = {float(s['game_time_seconds']): s for index, s in enumerate(samples)
             if index in allowed and s.get('status') == 'ok' and s.get('clan') == local['clan']
             and 0 <= s.get('game_time_seconds', -1) <= history['game_time_seconds'] + 1}
    ordered = sorted(clean.items())
    cap_changes = []
    previous = None
    for time, sample in ordered:
        cap = sample.get('population_cap')
        if cap is not None and previous and previous[1] != cap:
            cap_changes.append({
                'previous_sample_time': previous[0], 'observed_at': time,
                'before': previous[1], 'after': cap,
                'meaning': 'Change occurred within this sample interval.',
            })
        if cap is not None:
            previous = (time, cap)
    joined = []
    for event in events:
        time = event['time']
        start, stop = max(0, time - window), min(end, time + window)
        changes = {}
        for metric in METRICS:
            points = local['curves'].get(metric, [])
            before = curve_value(points, start, end)
            after = curve_value(points, stop, end)
            if before is not None and after is not None:
                changes[metric] = {'before': before, 'after': after,
                                   'net_observed_change': after['value'] - before['value']}
        joined.append({
            'native_event_index': event['index'], 'time': time, 'kind': event['kind'],
            'params': event['params'],
            'initial_state_event': time == 0,
            'event_semantics': 'completion' if event['kind'] == 'AddBuilding' else 'native_logged_event',
            'comparison_window': {'start': start, 'end': stop, 'metrics': changes},
            'other_native_events_in_window': [
                {'index': e['index'], 'time': e['time'], 'kind': e['kind']}
                for e in events if start <= e['time'] <= stop and e['index'] != event['index']],
            'nearby_observed_population_cap_changes': [
                c for c in cap_changes if abs(c['observed_at'] - time) <= window],
            'attribution': 'temporal_association_only',
        })
    return {
        'schema_version': 2, 'clan': local['clan'], 'game_build': history['game_build'],
        'session_key': history.get('session_key'), 'identity_join': identity_join,
        'game_time_seconds': history['game_time_seconds'], 'game_finished': history['game_finished'],
        'native_events_joined': len(joined), 'local_state_samples_used': len(ordered),
        'observed_population_cap_changes': cap_changes,
        'events': joined,
        'limits': [
            'Window stock differences include income, consumption, costs and other events; they are not the isolated effect of this action.',
            'AddBuilding is completion; its spending may precede the event by the entire construction period.',
            'The house/population-cap example combines native events with independently recorded state samples.',
            'Missing or conflicting runtime identities are rejected; multiple capture segments require explicit audited selection.',
            'Each choice, canceled action and job assignment is not captured by the native event list.',
            'Counterfactual outcomes require a separate calibrated economic model; this joiner does not generate them.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('history', type=Path)
    parser.add_argument('--samples', nargs='*', default=[])
    parser.add_argument('--window', type=int, default=30)
    parser.add_argument('--audited-provenance', type=Path,
                        help='Explicit evidence-backed identity migration/segment selection JSON; never overrides conflicting IDs')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    history = json.loads(args.history.read_text(encoding='utf-8'))
    samples = []
    paths = sorted({p for pattern in args.samples for p in glob.glob(pattern)})
    for path in paths:
        samples.extend(json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip())
    audited = json.loads(args.audited_provenance.read_text(encoding='utf-8')) if args.audited_provenance else None
    review = make_review(history, samples, args.window, audited_provenance=audited)
    review['provenance'] = {'history': str(args.history.resolve()), 'samples': [str(Path(p).resolve()) for p in paths]}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: review[k] for k in ('clan', 'native_events_joined', 'local_state_samples_used', 'observed_population_cap_changes')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
