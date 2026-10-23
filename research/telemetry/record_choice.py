"""Append a manually described choice to the active recording segment.

--capture-latest is mandatory. A fresh read-only snapshot validates its loaded
game and native-log lineage; the journal reuses the existing recording identity.
This does not record mouse input and never issues game commands.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'companion' / 'core'))
from capture_identity import identity_mismatch, native_fingerprint, native_log_checkpoint


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Capture timestamp missing')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Capture timestamp must include timezone')
    return result


def bind_capture_identity(latest, live, *, now=None, max_age_seconds=10, max_game_gap_seconds=5):
    """Pure validation used by the CLI and offline regression tests."""
    if latest.get('status') != 'ok' or live.get('status') != 'ok':
        raise ValueError('Both capture latest and live read must be valid match snapshots')
    for field in ('session_key', 'capture_run_id', 'capture_segment_id'):
        if not isinstance(latest.get(field), str) or not latest[field]:
            raise ValueError('Capture latest missing required ' + field)
    # A new validation Probe has its own run; compare the loaded game only.
    runtime_live = {key: value for key, value in live.items()
                    if key not in ('capture_run_id', 'capture_segment_id')}
    if identity_mismatch(latest, runtime_live):
        raise ValueError('Capture latest belongs to a different loaded game/player')
    now = now or datetime.now(timezone.utc)
    age = (now - _timestamp(latest.get('captured_at'))).total_seconds()
    live_age = (now - _timestamp(live.get('captured_at'))).total_seconds()
    if not -1 <= age <= max_age_seconds or not -1 <= live_age <= max_age_seconds:
        raise ValueError('Capture latest/live read is stale or has a future timestamp')
    before, after = latest.get('game_time_seconds'), live.get('game_time_seconds')
    if (not isinstance(before, (int, float)) or not isinstance(after, (int, float))
            or not math.isfinite(before) or not math.isfinite(after) or before < 0 or after < 0):
        raise ValueError('Invalid game time in capture latest/live read')
    gap = after - before
    if gap < -1e-7:
        raise ValueError('Game time rolled back after capture latest')
    if gap > max_game_gap_seconds:
        raise ValueError('Capture latest is too far behind live game time')
    if 'native_events' not in live:
        raise ValueError('Live read must provide native log for branch verification')
    if 'native_events' in latest:
        prefix = native_fingerprint(latest['native_events'])
        current = native_fingerprint(live['native_events'])
        if len(prefix) > len(current) or prefix != current[:len(prefix)]:
            raise ValueError('Native log replaced or truncated after capture latest')
    elif 'native_log_checkpoint' in latest:
        checkpoint = latest['native_log_checkpoint']
        count = checkpoint.get('event_count') if isinstance(checkpoint, dict) else None
        if type(count) is not int or not 0 <= count <= len(live['native_events']):
            raise ValueError('Invalid or truncated capture native log checkpoint')
        if native_log_checkpoint(live['native_events'][:count]) != checkpoint:
            raise ValueError('Native log lineage does not match capture checkpoint')
    else:
        raise ValueError('Capture latest must include native_events or native_log_checkpoint')
    return {**{field: latest[field] for field in ('session_key', 'capture_run_id', 'capture_segment_id')},
            'capture_segment_index': latest.get('capture_segment_index'),
            'capture_sample_captured_at': latest['captured_at'],
            'capture_sample_game_time_seconds': before,
            'capture_validation_game_gap_seconds': gap}


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture-latest', type=Path, required=True)
    parser.add_argument('--kind', required=True)
    parser.add_argument('--note', required=True)
    parser.add_argument('--target-uid', type=int)
    parser.add_argument('--target-kind')
    parser.add_argument('--zone', type=int)
    parser.add_argument('--status', choices=['issued', 'confirmed', 'failed', 'observation'], default='issued')
    parser.add_argument('--out', type=Path, default=HERE / 'choice_journal.ndjson')
    args = parser.parse_args()
    # Import the Windows reader only when actually recording, not during offline tests.
    sys.path.insert(0, str(HERE))
    from action_capture import ActionProbe, Reader, find_pid
    latest_bytes = args.capture_latest.read_bytes()
    latest = json.loads(latest_bytes.decode('utf-8-sig'))
    with Reader(find_pid()) as reader:
        live = ActionProbe(reader).sample()
    identity = bind_capture_identity(latest, live)
    # A concurrent collector may have started another branch during validation.
    current_latest = json.loads(args.capture_latest.read_text(encoding='utf-8-sig'))
    for field in ('session_key', 'capture_run_id', 'capture_segment_id'):
        if current_latest.get(field) != latest[field]:
            raise ValueError('Recording segment changed during journal validation; retry')
    bind_capture_identity(current_latest, live)
    target = {key: value for key, value in {'uid': args.target_uid, 'kind': args.target_kind,
                                           'zone_id': args.zone}.items() if value is not None}
    row = {'intent_id': uuid.uuid4().hex, 'source': 'explicit_operator_journal',
           'actor': 'human_or_assistant_operator', 'recorded_at': datetime.now(timezone.utc).isoformat(),
           **identity, 'game_time_seconds': live['game_time_seconds'],
           'kind': args.kind, 'status': args.status, 'target': target, 'note': args.note,
           'selected_units': [{'uid': unit['uid'], 'kind': unit['kind']} for unit in live['units'] if unit.get('selected')],
           'stocks_at_recording': live['stocks'], 'population_at_recording': live['population'],
           'population_cap_at_recording': live['population_cap'],
           'capture_provenance': {'path': str(args.capture_latest.resolve()),
                                  'sha256': hashlib.sha256(latest_bytes).hexdigest()}}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('a', encoding='utf-8') as out:
        out.write(json.dumps(row, ensure_ascii=False) + '\n')
    print(json.dumps(row, ensure_ascii=False))


if __name__ == '__main__':
    main()
