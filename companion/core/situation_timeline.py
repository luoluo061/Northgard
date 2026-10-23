"""Own staffing and situation observations, with no inferred input or cause.

An assignment is not proof of production. Counts retain target-zone and
activity evidence separately rather than calling every assigned worker busy.
"""
from __future__ import annotations

from copy import deepcopy
import math

IDENTITY = ('session_key', 'capture_run_id', 'capture_segment_id')
RESOURCES = ('Food', 'Wood', 'Money', 'Stone', 'Iron', 'Lore')


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def staffing(snapshot):
    """Consume the already whitelisted PlayerObservation, never raw entities."""
    if (snapshot.get('observation_type') != 'PlayerObservation' or snapshot.get('status') != 'ok'
            or snapshot.get('quality', {}).get('identity_verified') is not True):
        return {'status': 'unavailable', 'buildings': []}
    units = {u['uid']: u for u in snapshot.get('units', [])}
    result = []
    for building in snapshot.get('buildings', []):
        uid, zone = building['uid'], building.get('zone_id')
        assigned_list = set(building.get('assigned_unit_ids', [])) & units.keys()
        unit_targets = {unit_id for unit_id, unit in units.items()
                        if type(unit.get('assign_to')) is dict and unit['assign_to'].get('uid') == uid}
        corroborated = assigned_list & unit_targets
        in_zone, outside, approaching, active, unavailable = [], [], [], [], []
        for unit_id in sorted(corroborated):
            unit = units[unit_id]
            if unit.get('zone_id') == zone:
                in_zone.append(unit_id)
            elif unit.get('zone_id') is not None:
                outside.append(unit_id)
            else:
                unavailable.append(unit_id)
            going_to = unit.get('going_to')
            if (type(going_to) is dict and unit.get('zone_id') != zone
                    and (going_to.get('uid') == uid or going_to.get('zone_id') == zone)):
                approaching.append(unit_id)
            if (unit.get('zone_id') == zone and (unit.get('cur_job') or {}).get('kind') == 'Active'
                    and unit.get('enabled') is True and unit.get('has_no_work') is False
                    and type(unit.get('produce')) is str):
                active.append(unit_id)
        result.append({'building_uid': uid, 'zone_id': zone, 'building_kind': building.get('kind'),
                       'building_assignment_count': len(assigned_list),
                       'unit_target_assignment_count': len(unit_targets),
                       'assignment_lists_agree': assigned_list == unit_targets,
                       'corroborated_assigned_unit_ids': sorted(corroborated),
                       'corroborated_assigned_count': len(corroborated),
                       'assigned_in_target_zone_count': len(in_zone),
                       'assigned_outside_target_zone_count': len(outside),
                       'assigned_with_target_destination_count': len(approaching),
                       'active_resource_state_candidate_count': len(active),
                       'assignment_location_unknown_count': len(unavailable)})
    return {'schema_version': 1, 'status': 'ok', 'buildings': result,
            'interpretation': 'Assignment, location and activity flags are separate observations; active candidates are not measured per-worker output.'}


def _conditions(snapshot):
    result = []
    population, cap = snapshot.get('population'), snapshot.get('population_cap')
    if _number(population) and _number(cap) and cap > 0 and population >= cap:
        result.append('population_at_capacity_cache')
    for name in ('Food', 'Wood', 'Money'):
        stock = snapshot.get('stocks', {}).get(name)
        if _number(stock) and stock <= .025:
            result.append(name + '_stock_near_zero')
        row = snapshot.get('economy', {}).get('resources', {}).get(name, {})
        if snapshot.get('economy', {}).get('status') == 'ok':
            net = row.get('net_per_game_second')
            if _number(net) and net < 0:
                result.append(name + '_cached_net_negative')
            if (row.get('stock_cap_verified') is True and _number(stock)
                    and _number(row.get('stock_cap')) and stock >= row['stock_cap'] - .025):
                result.append(name + '_at_verified_stock_capacity')
    return sorted(result)


class SituationTracker:
    def __init__(self):
        self.previous = None

    def feed(self, snapshot):
        identities = {key: snapshot.get(key) for key in IDENTITY}
        result = {'schema_version': 1, **identities, 'events': [],
                  'interpretation': 'Sampled own state transitions, not guaranteed input or damage causes.'}
        if (snapshot.get('observation_type') != 'PlayerObservation' or snapshot.get('status') != 'ok'
                or snapshot.get('quality', {}).get('identity_verified') is not True
                or not all(identities.values()) or not _number(snapshot.get('game_time_seconds'))):
            self.previous = None
            result['record_kind'] = 'situation_gap'
            return result
        summary = staffing(snapshot)
        conditions = _conditions(snapshot)
        result.update(staffing=summary, conditions=conditions,
                      game_time_seconds=snapshot['game_time_seconds'])
        old = self.previous
        if (old is None or any(old.get(key) != snapshot.get(key) for key in IDENTITY)
                or old.get('local_player_entity_uid') != snapshot.get('local_player_entity_uid')
                or snapshot['game_time_seconds'] < old['game_time_seconds']
                or snapshot['game_time_seconds'] - old['game_time_seconds'] > 5):
            result['record_kind'] = 'situation_baseline'
        else:
            result['record_kind'] = 'situation_interval'
            interval = {'start_game_seconds': old['game_time_seconds'],
                        'end_game_seconds': snapshot['game_time_seconds']}
            result['time_interval'] = interval
            old_staff = {b['building_uid']: b for b in staffing(old)['buildings']}
            for now in summary['buildings']:
                before = old_staff.get(now['building_uid'])
                if before is None:
                    continue
                observed_keys = ('building_assignment_count', 'unit_target_assignment_count',
                                 'corroborated_assigned_unit_ids', 'assigned_in_target_zone_count',
                                 'assigned_outside_target_zone_count', 'assigned_with_target_destination_count',
                                 'active_resource_state_candidate_count', 'assignment_lists_agree')
                changes = {key: {'before': before[key], 'after': now[key]} for key in observed_keys
                           if before[key] != now[key]}
                if changes:
                    result['events'].append({'kind': 'building_staffing_state_changed',
                        'building_uid': now['building_uid'], 'zone_id': now['zone_id'], 'changes': changes})
            previous_conditions = set(_conditions(old))
            for name in sorted(set(conditions) - previous_conditions):
                result['events'].append({'kind': 'condition_started_observed', 'condition': name})
            for name in sorted(previous_conditions - set(conditions)):
                result['events'].append({'kind': 'condition_ended_observed', 'condition': name})
            if type(old.get('in_winter')) is bool and type(snapshot.get('in_winter')) is bool and old['in_winter'] != snapshot['in_winter']:
                result['events'].append({'kind': 'winter_state_changed', 'in_winter': snapshot['in_winter']})
            old_units = {u['uid']: u for u in old.get('units', [])}
            for unit in snapshot.get('units', []):
                before = old_units.get(unit['uid'])
                if before is None:
                    continue
                health0, health1 = before.get('hit_life'), unit.get('hit_life')
                if _number(health0) and _number(health1) and health0 != health1:
                    result['events'].append({'kind': 'unit_health_change_observed', 'unit_uid': unit['uid'],
                        'before': health0, 'after': health1, 'cause': 'unknown'})
        self.previous = deepcopy(snapshot)
        return result
