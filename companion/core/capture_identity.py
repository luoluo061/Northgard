"""Pure capture identity and conservative offline join rules.

A runtime session identifies one loaded game object, not a save file or a match
across restarts. Capture runs and rollback/log-reset segments are separate IDs.
No process addresses are exported: they contribute only to the opaque hash.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import uuid


IDENTITY_FIELDS = ('session_key', 'capture_run_id', 'capture_segment_id')


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def runtime_session_key(*, pid, game_address, state_address, local_player_address,
                        state_uid, local_player_uid, load_time, game_build):
    """Same deterministic algorithm for state, history and operator readers."""
    values = dict(pid=int(pid), game_address=int(game_address), state_address=int(state_address),
                  local_player_address=int(local_player_address), state_uid=int(state_uid),
                  local_player_uid=int(local_player_uid), load_time=load_time, game_build=game_build)
    if any(values[k] <= 0 for k in ('pid', 'game_address', 'state_address', 'local_player_address')):
        raise ValueError('Active process/game/player required for runtime identity')
    return hashlib.sha256(('northgard-runtime-v1:' + _canonical(values)).encode()).hexdigest()[:24]


def native_fingerprint(events):
    """Ignore exporter annotations; retain actual ordered native event content."""
    return [_canonical({k: event.get(k) for k in ('index', 'time', 'kind', 'params')}) for event in events]


def native_log_checkpoint(events):
    fingerprints = native_fingerprint(events)
    return {'event_count': len(fingerprints),
            'sha256': hashlib.sha256(_canonical(fingerprints).encode()).hexdigest()}


class CaptureIdentityTracker:
    """One instance per recording run; repeated reads do not create sessions."""
    def __init__(self, capture_run_id=None):
        self.capture_run_id = capture_run_id or uuid.uuid4().hex
        self.previous_session = None
        self.previous_time = None
        self.previous_prefix = []
        self.segment = 0

    def observe(self, session_key, game_time_seconds, native_events):
        if not isinstance(session_key, str) or not session_key:
            raise ValueError('Missing runtime session identity')
        at = float(game_time_seconds)
        if not math.isfinite(at) or at < 0:
            raise ValueError('Invalid game time')
        prefix = native_fingerprint(native_events)
        reason = None
        if self.previous_session is None:
            reason = 'initial_baseline'
        elif session_key != self.previous_session:
            reason = 'session_identity_changed'
        elif at < self.previous_time - 1e-7:
            reason = 'game_time_rollback'
        elif self.previous_prefix and (len(prefix) < len(self.previous_prefix)
                                      or prefix[:len(self.previous_prefix)] != self.previous_prefix):
            reason = 'native_log_replaced_or_truncated'
        if reason:
            self.segment += 1
        self.previous_session, self.previous_time, self.previous_prefix = session_key, at, prefix
        return {'session_key': session_key, 'capture_run_id': self.capture_run_id,
                'capture_segment_id': f'{self.capture_run_id}:{self.segment}',
                'capture_segment_index': self.segment, 'capture_segment_reason': reason,
                'native_log_checkpoint': native_log_checkpoint(native_events)}


def identity_mismatch(left, right, *, require_segment=False):
    """Return a reason instead of guessing from clan, time, seed or file name."""
    if not left.get('session_key') or not right.get('session_key'):
        return 'missing_session_identity'
    if left['session_key'] != right['session_key']:
        return 'session_identity_mismatch'
    for field in ('pid', 'local_player_entity_uid', 'clan'):
        if left.get(field) is not None and right.get(field) is not None and left[field] != right[field]:
            return field + '_mismatch'
    for field in ('capture_run_id', 'capture_segment_id'):
        a, b = left.get(field), right.get(field)
        if a and b and a != b:
            return field + '_mismatch'
        if require_segment and bool(a) != bool(b):
            return 'missing_' + field
    return None


def audited_identity(record, provenance=None):
    """Fill missing legacy IDs only after a named, evidence-backed manual audit.

    Existing contradictory IDs are never replaced. The audit is retained in the
    resulting review. This is an explicit migration mechanism, not inference.
    """
    result = deepcopy(record)
    if provenance is None:
        return result
    if not provenance.get('reviewed_by') or not provenance.get('evidence') or not provenance.get('session_key'):
        raise ValueError('Audited provenance requires reviewed_by, evidence and session_key')
    for field in IDENTITY_FIELDS:
        if provenance.get(field) and not result.get(field):
            result[field] = provenance[field]
    return result


def history_join_decisions(history, samples, audited_provenance=None):
    """Identify a unique compatible capture segment for an external history.

    Returns allowed input indices and reviewable rejection reasons. Multiple
    recording runs or rollback segments are ambiguous unless an audited source
    selection names sample_segment_index (the one-based ordered segment).
    """
    history = audited_identity(history, audited_provenance)
    local_players = [player for player in history.get('players', ()) if player.get('local_player')]
    if len(local_players) == 1 and local_players[0].get('clan'):
        history.setdefault('clan', local_players[0]['clan'])
    tracker = CaptureIdentityTracker('offline-join')
    previous_context = None
    segment = 0
    groups, decisions = {}, []
    history_prefix = native_fingerprint(history.get('events', ()))
    for index, original in enumerate(samples):
        sample = audited_identity(original, audited_provenance)
        if sample.get('status', 'ok') != 'ok':
            decisions.append({'sample_index': index, 'accepted': False, 'reason': 'sample_not_ok'})
            continue
        at = sample.get('game_time_seconds')
        if not isinstance(at, (int, float)) or not math.isfinite(at) or at < 0:
            decisions.append({'sample_index': index, 'accepted': False, 'reason': 'invalid_game_time'})
            continue
        # Preserve ordered boundaries even when the external history is missing.
        context = tuple(sample.get(f) for f in (*IDENTITY_FIELDS, 'pid', 'clan', 'local_player_entity_uid'))
        ident = tracker.observe(sample.get('session_key') or '<missing>', at, sample.get('native_events', ()))
        if previous_context != context or ident['capture_segment_reason']:
            segment += 1
        previous_context = context
        reason = identity_mismatch(history, sample)
        if reason is None and at > history.get('game_time_seconds', -1) + 1:
            reason = 'sample_after_history'
        if reason is None and 'native_events' in sample:
            prefix = native_fingerprint(sample['native_events'])
            if len(prefix) > len(history_prefix) or prefix != history_prefix[:len(prefix)]:
                reason = 'native_log_lineage_mismatch'
        decision = {'sample_index': index, 'sample_segment_index': segment,
                    'accepted': reason is None, 'reason': reason}
        decisions.append(decision)
        if reason is None:
            groups.setdefault(segment, []).append(decision)
    selected = (audited_provenance or {}).get('sample_segment_index')
    for key, rows in groups.items():
        if (selected is not None and key != selected) or (selected is None and len(groups) > 1):
            for row in rows:
                row.update(accepted=False, reason='ambiguous_capture_segments' if selected is None else 'outside_audited_segment')
    allowed = {row['sample_index'] for row in decisions if row['accepted']}
    return allowed, {'accepted_sample_count': len(allowed), 'decisions': decisions,
                     'audited_provenance': deepcopy(audited_provenance),
                     'rule': 'same runtime session and one unambiguous capture segment; no clan/time inference'}
