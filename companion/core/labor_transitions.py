"""Link sampled own-unit reassignment milestones in game time.

These are observations between samples, not intercepted commands. The tracker
does not equate produce/arrival/occupation with actual credited resource output,
or disappearance with death. It never bridges captures, read errors or long gaps.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

IDENTITIES = ('session_key', 'capture_run_id', 'capture_segment_id')
_TOKEN = re.compile(r'[A-Za-z][A-Za-z0-9_.]{0,95}\Z')
_IDENTITY = re.compile(r'[A-Za-z0-9_:-]{1,128}\Z')
RESOURCES = frozenset(('Food', 'Wood', 'Money', 'Stone', 'Iron', 'Lore',
                       'Wheat', 'Fish', 'Meat', 'Sailors', 'Healing'))
MILITARY = frozenset(('Warrior', 'ShieldBearer', 'AxeWielder', 'AxeThrower'))
WORKERS = frozenset(('Villager', 'Scout', 'Woodcutter', 'Farmer', 'Hunter',
                     'Fisherman', 'Miner', 'Sailor', 'Merchant', 'RuneMaster', 'Runemaster',
                     'Healer', 'Smith', 'InnKeeper', 'Brewer'))


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) < 2**53


def _id(value):
    return type(value) is int and 0 <= value < 2**31


def _token(value):
    return type(value) is str and _TOKEN.fullmatch(value) is not None


def _classification(kind):
    if kind == 'BearMaiden':
        return 'special_armored_bear'
    if kind in ('Maiden', 'Maiden02'):
        return 'hero'
    if kind in ('Bodyguard', 'Bodyguard02'):
        return 'special_bodyguard'
    if kind in MILITARY:
        return 'ordinary_military_kind'
    if kind in WORKERS:
        return 'worker_kind'
    return 'unclassified_kind'


def _reference(value, entities, known_zones):
    if value is None:
        return None
    if type(value) is not dict:
        raise ValueError('Invalid local reference')
    uid = value.get('uid')
    if _id(uid) and uid in entities:
        entity = entities[uid]
        return {'uid': uid, 'kind': entity['kind'], 'zone_id': entity['zone_id'],
                'entity_scope': entity['entity_scope']}
    zid = value.get('zone_id')
    if (value.get('entity_scope') == 'discovered_zone_id_only'
            and _id(zid) and zid in known_zones):
        return {'uid': None, 'kind': None, 'zone_id': zid,
                'entity_scope': 'discovered_zone_id_only'}
    raise ValueError('Unverified local reference')


def _normalized_units(observation):
    units, buildings = observation.get('units'), observation.get('buildings')
    known, owned = observation.get('discovered_zone_ids'), observation.get('owned_zone_ids')
    if (type(units) is not list or len(units) > 10000 or type(buildings) is not list
            or type(known) is not list or type(owned) is not list
            or not all(_id(z) for z in known + owned)):
        raise ValueError('Missing local unit/zone scope')
    known_zones = set(known) | set(owned)
    entities = {}
    for category, rows in (('own_unit', units), ('owned_zone_building', buildings)):
        for row in rows:
            if (type(row) is not dict or not _id(row.get('uid')) or not _token(row.get('kind'))
                    or row['uid'] in entities
                    or (row.get('zone_id') is not None and row['zone_id'] not in known_zones)
                    or (category == 'owned_zone_building' and row.get('zone_id') not in owned)):
                raise ValueError('Invalid projected entity scope')
            entities[row['uid']] = {'kind': row['kind'], 'zone_id': row.get('zone_id'),
                                    'entity_scope': category}
    output = {}
    for row in units:
        state = {'uid': row['uid'], 'kind': row['kind'], 'zone_id': row.get('zone_id')}
        for field in ('assign_to', 'going_to'):
            state[field] = _reference(row.get(field), entities, known_zones)
        for field in ('assigned_job', 'cur_job'):
            job = row.get(field)
            if job is not None and (type(job) is not dict or not _token(job.get('kind'))):
                raise ValueError('Invalid projected task')
            state[field] = job.get('kind') if job else None
        for field in ('enabled', 'has_no_work'):
            value = row.get(field)
            if value is not None and type(value) is not bool:
                raise ValueError('Invalid projected work flag')
            state[field] = value
        state['produce'] = row.get('produce') if row.get('produce') in RESOURCES else None
        state['target_zone_id'] = row.get('target_zone_id')
        if state['target_zone_id'] is not None and state['target_zone_id'] not in known_zones:
            raise ValueError('Unverified target zone')
        position = row.get('position')
        state['position'] = ({'x': position['x'], 'y': position['y']}
            if type(position) is dict and _number(position.get('x')) and _number(position.get('y'))
            else None)
        output[row['uid']] = state
    return output


def _brief(state):
    return {key: deepcopy(state.get(key)) for key in (
        'kind', 'zone_id', 'assign_to', 'going_to', 'assigned_job', 'cur_job',
        'target_zone_id', 'produce', 'enabled', 'has_no_work', 'position')}


def _candidate(state):
    return (state.get('produce') is not None and state.get('enabled') is True
            and state.get('has_no_work') is False)


def _assignment_signature(state):
    ref = state['assign_to']
    target = (('uid', ref['uid']) if ref and ref['uid'] is not None else
              ('zone', ref['zone_id']) if ref else None)
    # TargetZone is a route waypoint and may clear on arrival; it is not a
    # new assignment then. A target entity's mutable kind/zone is not its ID.
    return (target, state['assigned_job'])


class LaborTransitionTracker:
    """``update(PlayerObservation) -> list[observed milestone]``.

    Call for *every* collector iteration, including error/rejected rows. Each
    result has sampled event bounds ``(previous_game_seconds, observed_by]``.
    Elapsed lower/upper bounds propagate both start and milestone uncertainty.
    Every transition must begin after an observed baseline for the same UID.
    """
    def __init__(self, max_gap_game_seconds=5):
        if not _number(max_gap_game_seconds) or max_gap_game_seconds <= 0:
            raise ValueError('Positive finite max gap required')
        self.max_gap = max_gap_game_seconds
        self._previous = None
        self._active = {}
        self._sequence = 0
        self._sample_index = 0

    def reset(self):
        self._previous = None
        self._active.clear()

    def _event(self, transition, phase, start, end, state, reason=None):
        bounds = transition['assignment_window']
        elapsed = (None if phase in ('assignment_changed_observed', 'assignment_cleared_observed')
                   else {'lower': max(0., start - bounds['observed_by_game_seconds']),
                         'upper': max(0., end - bounds['previous_game_seconds'])})
        return {
            'schema_version': 1, 'event_type': 'labor_transition_milestone',
            'source': 'own_unit_sampled_state_transition',
            'transition_id': transition['transition_id'], 'unit_uid': transition['uid'],
            'phase': phase, 'reason_code': reason,
            'unit_class_before': _classification(transition['before']['kind']),
            'unit_class_now': _classification(state['kind']),
            'assignment_observed_window': deepcopy(bounds),
            'milestone_observed_window': {
                'previous_game_seconds': start, 'observed_by_game_seconds': end,
                'sample_interval_game_seconds': end - start,
                'lower_exclusive': end > start},
            'elapsed_from_assignment_game_seconds_bounds': elapsed,
            'assignment_sample_index': transition['sample_index'],
            'milestone_sample_index': self._sample_index,
            'before': _brief(transition['before']), 'now': _brief(state),
            'actual_resource_production_confirmed': False,
            'duration_semantics': 'sample_bounded_assignment_to_observed_stage_not_pure_walking_time',
            'unknown_fields': ['exact_command_time', 'player_intent', 'pure_travel_duration',
                               'actual_resource_credit', 'unit_population_weight'],
            **dict(zip(IDENTITIES, transition['identity'])),
        }

    def _stop(self, reason):
        events = []
        if self._previous:
            _, when, units = self._previous
            for transition in self._active.values():
                state = units.get(transition['uid'], transition['before'])
                events.append(self._event(transition, 'tracking_incomplete', when, when,
                                          state, reason))
        self.reset()
        return events

    def finish(self):
        """Flush unresolved transitions when recording stops, without guessing."""
        return self._stop('recording_finished_before_next_milestone')

    def update(self, observation):
        self._sample_index += 1
        if (type(observation) is not dict or observation.get('status') != 'ok'
                or observation.get('observation_type') != 'PlayerObservation'
                or observation.get('scope') != 'local_player_and_owned_zones'
                or type(observation.get('quality')) is not dict
                or observation.get('quality', {}).get('identity_verified') is not True
                or not _id(observation.get('local_player_entity_uid'))
                or observation['local_player_entity_uid'] == 0 or not _token(observation.get('clan'))
                or not all(type(observation.get(k)) is str and _IDENTITY.fullmatch(observation[k])
                           for k in IDENTITIES)
                or not _number(observation.get('game_time_seconds'))
                or observation['game_time_seconds'] < 0):
            return self._stop('observation_error_or_scope_unavailable')
        try:
            units = _normalized_units(observation)
        except (TypeError, ValueError, KeyError):
            return self._stop('projected_unit_fields_unavailable')
        identity = tuple(observation[k] for k in IDENTITIES)
        context = (identity, observation['local_player_entity_uid'], observation['clan'])
        when = observation['game_time_seconds']
        old = self._previous
        events = []
        if old and context != old[0]:
            events.extend(self._stop('capture_identity_changed'))
            old = None
        if old and (when < old[1] or when - old[1] > self.max_gap):
            events.extend(self._stop('game_clock_rollback_or_sample_gap'))
            old = None
        self._previous = (context, when, units)
        if old is None:
            return events
        _, start, previous_units = old
        for uid in list(self._active):
            if uid not in units:
                transition = self._active.pop(uid)
                events.append(self._event(transition, 'tracking_incomplete', start, when,
                    previous_units.get(uid, transition['before']), 'unit_not_observed_cause_unknown'))
        for uid, state in units.items():
            before = previous_units.get(uid)
            if before is None:
                continue  # Appearance does not establish birth/recruitment.
            changed_assignment = _assignment_signature(before) != _assignment_signature(state)
            # A previously unverified/omitted reference turning null does not
            # prove a cancellation. Treat it only as an observed clear state.
            if changed_assignment:
                previous_transition = self._active.pop(uid, None)
                if previous_transition:
                    events.append(self._event(previous_transition, 'tracking_incomplete',
                        start, when, state, 'new_assignment_before_confirmed_output'))
                self._sequence += 1
                transition = {
                    'transition_id': f'labor:{identity[2]}:{uid}:{self._sequence}',
                    'uid': uid, 'before': deepcopy(before), 'identity': identity,
                    'assignment_window': {'previous_game_seconds': start,
                        'observed_by_game_seconds': when,
                        'sample_interval_game_seconds': when - start,
                        'lower_exclusive': when > start},
                    'sample_index': self._sample_index,
                    'seen': set(), 'candidate_absence_seen': not _candidate(state),
                }
                self._active[uid] = transition
                phase = ('assignment_cleared_observed' if state['assign_to'] is None
                         and before['assign_to'] is not None else 'assignment_changed_observed')
                events.append(self._event(transition, phase, start, when, state))
            transition = self._active.get(uid)
            if transition is None:
                continue
            if not _candidate(state):
                transition['candidate_absence_seen'] = True
            milestones = []
            if (before['going_to'] != state['going_to']
                    or before['assigned_job'] != state['assigned_job']
                    or before['cur_job'] != state['cur_job']):
                milestones.append('task_or_route_changed_observed')
            if (before['zone_id'] != state['zone_id'] or before['position'] != state['position']):
                milestones.append('location_changed_observed')
            if before['kind'] != state['kind']:
                milestones.append('occupation_changed_observed')
            if (_candidate(state)
                    and ((transition['candidate_absence_seen'] and not changed_assignment)
                         or state['produce'] != transition['before']['produce']
                         or state['kind'] != transition['before']['kind'])):
                milestones.append('production_candidate_resumed_observed')
            for phase in milestones:
                if phase not in transition['seen']:
                    events.append(self._event(transition, phase, start, when, state))
                    transition['seen'].add(phase)
            if 'production_candidate_resumed_observed' in milestones:
                self._active.pop(uid, None)
        return events
