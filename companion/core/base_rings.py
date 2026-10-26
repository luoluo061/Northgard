"""Conservative base rings from a whitelisted PlayerObservation's known map.

Distances are shortest paths in the *currently observed reciprocal geometric
graph*. They say nothing about traversal, ownership, or the complete hidden
map. No process reader, historical map cache, or production model is used.
"""
from __future__ import annotations

from collections import Counter, deque
from copy import deepcopy
import math
import re

try:
    from .map_projection import (SOURCE as MAP_SOURCE, HLBOOT_SHA256,
                                 DATA_CDB_SHA256)
except ImportError:
    from map_projection import (SOURCE as MAP_SOURCE, HLBOOT_SHA256,
                                DATA_CDB_SHA256)


SOURCE = 'derived_known_geometric_map_adjacency'
POLICY_VERSION = 'base-neighborhood.v1'
IDENTITIES = ('session_key', 'capture_run_id', 'capture_segment_id')
_IDENTITY = re.compile(r'[A-Za-z0-9_:-]{1,128}\Z')
_TOKEN = re.compile(r'[A-Za-z][A-Za-z0-9_.]{0,95}\Z')
_ADJACENCY = 'geometric_map_cell_neighbors_not_traversability'


def _id(value):
    return type(value) is int and 0 <= value < 2**31


def _uid(value):
    return type(value) is int and 0 < value < 2**53


def _number(value):
    return (type(value) is int and abs(value) < 2**53
            or type(value) is float and math.isfinite(value) and abs(value) < 2**53)


def _ids(value):
    return (type(value) is list and len(value) <= 10000
            and all(_id(item) for item in value) and len(value) == len(set(value)))


def _verified(observation):
    return (type(observation) is dict
            and type(observation.get('schema_version')) is int
            and observation['schema_version'] == 1
            and observation.get('observation_type') == 'PlayerObservation'
            and observation.get('policy_version') == 'player-observation.v1'
            and observation.get('status') == 'ok'
            and observation.get('scope') == 'local_player_and_owned_zones'
            and type(observation.get('quality')) is dict
            and observation['quality'].get('identity_verified') is True
            and _uid(observation.get('local_player_entity_uid'))
            and all(type(observation.get(key)) is str
                    and _IDENTITY.fullmatch(observation[key]) for key in IDENTITIES)
            and _number(observation.get('game_time_seconds'))
            and observation['game_time_seconds'] >= 0
            and _ids(observation.get('discovered_zone_ids'))
            and _ids(observation.get('owned_zone_ids'))
            and set(observation['owned_zone_ids']) <= set(observation['discovered_zone_ids']))


def _empty(reason):
    return {'schema_version': 1, 'policy_version': POLICY_VERSION,
            'status': 'unknown', 'source': SOURCE,
            'scope': 'local_player_known_base_neighborhood',
            'adjacency_semantics': _ADJACENCY,
            'distance_semantics': 'shortest_path_in_current_known_reciprocal_graph',
            'coverage': 'incomplete',
            'coverage_semantics': 'empty_rings_mean_not_observed_not_absent',
            'anchor': None, 'ring0_zone_ids': [], 'ring1_zone_ids': [],
            'ring2_zone_ids': [], 'known_unclassified_zone_ids': [], 'zones': [],
            'reason_codes': [reason],
            'unknown_fields': ['complete_base_neighborhood', 'current_visibility',
                               'traversability', 'non_owned_zone_owner',
                               'original_spawn_zone'],
            'continuity': 'current_sample_only'}


def _own_annotation(row, own_uid):
    for key in ('owner_uid', 'owner_entity_uid', 'player_uid', 'player_entity_uid'):
        if key in row and (not _uid(row[key]) or row[key] != own_uid):
            return False
    return ('owner' not in row or row['owner'] == 'self'
            or _uid(row['owner']) and row['owner'] == own_uid)


def _entities(observation):
    """Only whitelisted own records, with ambiguous UID chains omitted."""
    own = observation['local_player_entity_uid']
    known, owned = set(observation['discovered_zone_ids']), set(observation['owned_zone_ids'])
    valid, reasons = {'units': [], 'buildings': []}, set()
    rows = []
    for field in valid:
        values = observation.get(field)
        if type(values) is not list or len(values) > 10000:
            reasons.add('own_entity_references_unavailable')
            continue
        for row in values:
            if (type(row) is not dict or not _uid(row.get('uid'))
                    or type(row.get('kind')) is not str or not _TOKEN.fullmatch(row['kind'])
                    or not _own_annotation(row, own)
                    or not _id(row.get('zone_id')) or row['zone_id'] not in known
                    or field == 'buildings' and row['zone_id'] not in owned):
                reasons.add('unverified_entity_reference_omitted')
                continue
            rows.append((field, row))
    counts = Counter(row['uid'] for _, row in rows)
    for field, row in rows:
        if counts[row['uid']] == 1:
            # Never retain mutable caller values, arbitrary entity fields, or jobs.
            clean = {'uid': row['uid'], 'kind': row['kind'], 'zone_id': row['zone_id']}
            if field == 'buildings' and type(row.get('is_construction')) is bool:
                clean['is_construction'] = row['is_construction']
            valid[field].append(clean)
        else:
            reasons.add('duplicate_own_entity_uid_omitted')
    return valid, reasons


def _new_anchor(observation, entities):
    candidates = [row for row in entities['buildings']
                  if row['kind'] == 'TownHall' and row.get('is_construction') is False]
    if len(candidates) != 1:
        return None, ('own_completed_town_hall_ambiguous' if len(candidates) > 1
                      else 'own_completed_town_hall_unavailable')
    hall = candidates[0]
    return {'session_key': observation['session_key'],
            'local_player_entity_uid': observation['local_player_entity_uid'],
            'zone_id': hall['zone_id'], 'town_hall_uid': hall['uid'],
            'first_observed_game_time_seconds': observation['game_time_seconds']}, None


def _valid_anchor(anchor, observation):
    return (type(anchor) is dict
            and anchor.get('session_key') == observation['session_key']
            and _uid(anchor.get('local_player_entity_uid'))
            and anchor['local_player_entity_uid'] == observation['local_player_entity_uid']
            and _id(anchor.get('zone_id')) and _uid(anchor.get('town_hall_uid'))
            and _number(anchor.get('first_observed_game_time_seconds'))
            and 0 <= anchor['first_observed_game_time_seconds'] <= observation['game_time_seconds'])


def _map_graph(observation):
    component = observation.get('map_observation')
    if (type(component) is not dict or type(component.get('schema_version')) is not int
            or component['schema_version'] != 1 or component.get('status') != 'ok'
            or component.get('policy_version') != 'player-map.v1'
            or component.get('source') != MAP_SOURCE
            or component.get('scope') != 'local_player_discovered_static_cells'
            or component.get('adjacency_semantics') != _ADJACENCY
            or component.get('hlboot_sha256') != HLBOOT_SHA256
            or component.get('data_cdb_sha256') != DATA_CDB_SHA256
            or any(component.get(key) != observation[key] for key in IDENTITIES)
            or not _uid(component.get('local_player_entity_uid'))
            or component['local_player_entity_uid'] != observation['local_player_entity_uid']
            or type(component.get('map_identity')) is not dict
            or component['map_identity'].get('map_instance_id') != observation['session_key']
            or not _number(component.get('game_time_seconds'))
            or not -0.025 <= observation['game_time_seconds'] - component['game_time_seconds'] <= 5
            or type(component.get('known_zones')) is not list
            or len(component['known_zones']) > 10000):
        return None, None, None, {'known_map_unavailable_or_unverified'}
    known = set(observation['discovered_zone_ids'])
    rows, reasons = {}, set()
    duplicate = set()
    for zone in component['known_zones']:
        if type(zone) is not dict or not _id(zone.get('id')) or zone['id'] not in known:
            reasons.add('unverified_map_zone_omitted')
            continue
        zid = zone['id']
        if zid in rows:
            duplicate.add(zid)
            reasons.add('duplicate_map_zone_omitted')
        else:
            rows[zid] = zone
    for zid in duplicate:
        del rows[zid]
    if set(rows) != known:
        reasons.add('known_map_rows_incomplete')
    directed = {zid: set() for zid in rows}
    for zid, row in rows.items():
        values = row.get('known_geometric_neighbor_ids')
        if type(values) is not list or len(values) > 10000:
            reasons.add('invalid_geometric_edges_omitted')
            continue
        seen = set()
        for neighbor in values:
            if not _id(neighbor) or neighbor == zid or neighbor not in rows:
                reasons.add('invalid_geometric_edges_omitted')
                continue
            if neighbor in seen:
                reasons.add('duplicate_geometric_edges_deduplicated')
            seen.add(neighbor)
        directed[zid] = seen
    graph = {zid: set() for zid in rows}
    for zid, neighbors in directed.items():
        for neighbor in neighbors:
            if zid in directed[neighbor]:
                graph[zid].add(neighbor)
            else:
                reasons.add('asymmetric_geometric_edges_omitted')
    return rows, graph, component['game_time_seconds'], reasons


def derive_base_neighborhood(observation, *, anchor=None):
    """Derive one independent component without retaining any previous map.

    ``anchor`` is tracker state (or a prior explicitly verified anchor), not an
    arbitrary coordinate. A call without it uses the current unique completed
    own TownHall. Unknown and malformed optional inputs never raise or invalidate
    the caller's core observation. Only reciprocal, known geometric edges count.
    """
    if not _verified(observation):
        return _empty('verified_player_observation_unavailable')
    result = _empty('known_graph_only_complete_rings_unverified')
    result.update({key: observation[key] for key in IDENTITIES})
    result['local_player_entity_uid'] = observation['local_player_entity_uid']
    result['game_time_seconds'] = observation['game_time_seconds']
    entities, reasons = _entities(observation)
    if anchor is None:
        anchor, reason = _new_anchor(observation, entities)
        if reason:
            reasons.add(reason)
    elif not _valid_anchor(anchor, observation):
        anchor = None
        reasons.add('base_anchor_unverified_or_outside_current_clock')
    rows, graph, map_when, map_reasons = _map_graph(observation)
    reasons |= map_reasons
    result['map_observed_game_time_seconds'] = map_when
    known = set(rows) if rows is not None else set()
    paths = {}
    if anchor is not None:
        zid = anchor['zone_id']
        own_hall = next((row for row in entities['buildings']
                        if row['uid'] == anchor['town_hall_uid']
                        and row['zone_id'] == zid and row['kind'] == 'TownHall'
                        and row.get('is_construction') is False), None)
        result['anchor'] = {
            'selection': 'first_observed_own_town_hall', 'policy': 'fixed_within_session',
            'original_spawn_status': 'unverified',
            'zone_id': zid if zid in known else None,
            'town_hall_uid': own_hall['uid'] if own_hall is not None else None,
            'town_hall_reference_status': ('observed_own_completed' if own_hall is not None
                                          else 'not_currently_observed'),
            'first_observed_game_time_seconds': anchor['first_observed_game_time_seconds']}
        if own_hall is None:
            reasons.add('anchor_town_hall_not_currently_observed')
        if zid not in known:
            reasons.add('anchor_zone_not_in_current_known_map')
        else:
            paths[zid] = [zid]
            queue = deque([zid])
            while queue:
                current = queue.popleft()
                if len(paths[current]) == 3:
                    continue
                for neighbor in sorted(graph[current]):
                    if neighbor not in paths:
                        paths[neighbor] = paths[current] + [neighbor]
                        queue.append(neighbor)
            result['status'] = 'partial'
    for ring in range(3):
        result['ring%d_zone_ids' % ring] = sorted(zid for zid, path in paths.items()
                                                  if len(path) - 1 == ring)
    result['known_unclassified_zone_ids'] = sorted(known - paths.keys())
    owned = set(observation['owned_zone_ids'])
    for zid in sorted(known):
        path = paths.get(zid)
        result['zones'].append({
            'zone_id': zid, 'exploration': 'known', 'current_visibility': 'unverified',
            'ownership': 'self' if zid in owned else 'unknown',
            'observed_graph_distance': len(path) - 1 if path is not None else None,
            'witness_path_zone_ids': list(path) if path is not None else [],
            'own_unit_uids': sorted(row['uid'] for row in entities['units'] if row['zone_id'] == zid),
            'own_building_uids': sorted(row['uid'] for row in entities['buildings'] if row['zone_id'] == zid),
            'map_observed_game_time_seconds': map_when,
            'own_entities_observed_game_time_seconds': observation['game_time_seconds']})
    result['reason_codes'] = sorted(set(result['reason_codes']) | reasons)
    return result


class BaseRingTracker:
    """Fix the first unique completed own TownHall's zone for one session.

    Call for every collector iteration, including errors. Gaps, pauses, capture
    boundaries and rollback never reuse a prior graph or entity snapshot. The
    anchor alone persists within the same session; a clock before its first
    observation cannot use it. New sessions or local-player identities reset it.
    """
    def __init__(self, max_gap_game_seconds=5):
        if not _number(max_gap_game_seconds) or max_gap_game_seconds <= 0:
            raise ValueError('Positive finite max gap required')
        self.max_gap = max_gap_game_seconds
        self.reset()

    def reset(self):
        self._context = None
        self._anchor = None
        self._previous = None
        self._had_error = False

    def update(self, observation):
        if not _verified(observation):
            self._previous = None
            self._had_error = True
            return _empty('verified_player_observation_unavailable')
        context = (observation['session_key'], observation['local_player_entity_uid'])
        if context != self._context:
            self.reset()
            self._context = context
        if self._anchor is None:
            entities, _ = _entities(observation)
            self._anchor, _ = _new_anchor(observation, entities)
        result = derive_base_neighborhood(observation, anchor=self._anchor)
        when = observation['game_time_seconds']
        identity = tuple(observation[key] for key in IDENTITIES)
        old = self._previous
        continuity = 'baseline_after_read_error' if self._had_error else 'initial_baseline'
        if old is not None:
            old_identity, old_when = old
            if identity != old_identity:
                continuity = 'capture_boundary_current_sample_only'
            elif when < old_when:
                continuity = 'clock_rollback_current_sample_only'
            elif when - old_when > self.max_gap:
                continuity = 'sample_gap_current_sample_only'
            elif when == old_when:
                continuity = 'paused_clock_current_sample_only'
            else:
                continuity = 'current_sample_only'
        result['continuity'] = continuity
        self._previous = (identity, when)
        self._had_error = False
        return deepcopy(result)
