"""Strict own-player growth projection and cautious interval-derived estimates.

No game-memory imports. Native stored inputs, static formula calculations and
interval estimates are separate. Missing bonuses are never replaced with 1.
"""
from __future__ import annotations

import math
import re

SOURCE = 'native_own_growth_inputs_read_only'
HLBOOT_SHA256 = 'db0459d251879698420f58f6819e0ab2f75a3cde6e65611f2ad723f3b4b7ef76'
DATA_CDB_SHA256 = '88d4c84b9622ce237533afc8674174aa9006d87a03258cd7e5fd671f930174e3'
IDENTITIES = ('session_key', 'capture_run_id', 'capture_segment_id')
IDENTITY = re.compile(r'[A-Za-z0-9_:-]{1,128}\Z')
SUPPORTED_THRESHOLD_CLANS = frozenset(('Bear', 'Raven'))
GROWTH_TECHS = ('Recruit', 'EndlessTide', 'PlotThrone')
NATIVE_KINDS = {
    'resource_growth_progress': 'nonnegative', 'current_happiness': 'number',
    'population_cache': 'integer', 'population_cap_cache': 'integer',
    'reserve_population_cache': 'integer', 'recruit_stack': 'nonnegative',
    'force_recruit': 'bool', 'low_housing_timer': 'nonnegative',
    'low_happiness_timer': 'nonnegative', 'no_population_growth_time': 'nonnegative',
    'has_conceded': 'bool', 'player_game_over': 'bool', 'game_finished': 'bool',
    'debug_population_can_grow': 'bool', 'growth_disabled_modifier': 'bool',
}
UNKNOWN = ('growth_bonus', 'current_computed_happiness', 'town_hall_under_attack',
           'complete_growth_block_reason', 'birth_spawn_eligibility',
           'all_growth_modifier_values')


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) < 2**53


def _valid(value, kind):
    if value is None:
        return True
    if kind == 'bool':
        return type(value) is bool
    if kind == 'integer':
        return type(value) is int and 0 <= value < 2**31
    return _number(value) and (kind != 'nonnegative' or value >= 0)


def _reject(code):
    return {'schema_version': 1, 'status': 'rejected', 'source': SOURCE,
            'scope': 'no_growth_data', 'reason_codes': [code],
            'unknown_fields': ['growth']}


def _verified_ordinary_mode(snapshot):
    """A missing or simplified context never proves the audited native path.

    This projection runs against the raw parent, before its game-context enum
    arguments are removed for sharing. Unknown, custom, editor and special
    modes retain native observations but receive no standard recruitment model.
    """
    context = snapshot.get('game_context')
    return (type(context) is dict
            and context.get('mode') == {
                'kind': 'Singleplayer', 'args': [{'kind': 'FreeForAll', 'args': []}]}
            and context.get('bifrost_data_present') is False
            and context.get('editor_present') is False)


def economic_cache_not_ready(value, snapshot):
    """Recognize only the pinned reader's explicit uninitialized-cache evidence.

    A missing component, generic read error or unsupported layout is not proof
    that a zero-valued housing cache is still initializing.
    """
    return (type(value) is dict and type(snapshot) is dict
            and type(value.get('schema_version')) is int and value['schema_version'] == 1
            and value.get('status') == 'not_ready'
            and value.get('source') == 'native_economic_caches_read_only'
            and value.get('hlboot_sha256') == HLBOOT_SHA256
            and value.get('reason_codes') == ['economic_cache_not_initialized']
            and all(type(snapshot.get(k)) is str and IDENTITY.fullmatch(snapshot[k])
                    and value.get(k) == snapshot[k] for k in IDENTITIES))


def population_cap_bootstrap_unavailable(snapshot):
    """Conservative audited opening only; never infer a replacement capacity."""
    pop = snapshot.get('population')
    owned = snapshot.get('owned_zone_ids')
    buildings = snapshot.get('buildings')
    return (snapshot.get('status') == 'ok'
            and _number(snapshot.get('game_time_seconds')) and snapshot['game_time_seconds'] == 0
            and type(pop) is int and pop > 0
            and type(snapshot.get('population_cap')) is int and snapshot['population_cap'] == 0
            and snapshot.get('clan') in SUPPORTED_THRESHOLD_CLANS
            and _verified_ordinary_mode(snapshot)
            and type(owned) is list and type(buildings) is list
            and any(type(b) is dict and b.get('kind') == 'TownHall'
                    and b.get('zone_id') in owned and b.get('is_construction') is False
                    for b in buildings)
            and economic_cache_not_ready(snapshot.get('economy'), snapshot))


def project_growth(value, snapshot):
    """Whitelist a raw component against a verified parent ActionProbe row."""
    if (type(value) is not dict or type(snapshot) is not dict
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value.get('source') != SOURCE or value.get('status') not in ('ok', 'partial')
            or snapshot.get('status') != 'ok'):
        return _reject('invalid_or_racing_growth_component')
    for key in IDENTITIES:
        if (type(snapshot.get(key)) is not str or not IDENTITY.fullmatch(snapshot[key])
                or value.get(key) != snapshot[key]):
            return _reject('growth_capture_identity_mismatch')
    if (value.get('hlboot_sha256') != HLBOOT_SHA256
            or value.get('data_cdb_sha256') != DATA_CDB_SHA256):
        return _reject('growth_build_unverified')
    uid = snapshot.get('local_player_entity_uid')
    own = snapshot.get('local_player_identity')
    if (type(uid) is not int or uid <= 0 or type(own) is not dict
            or own.get('verified') is not True or own.get('role') != 'player'
            or own.get('entity_uid') != uid or own.get('is_ai') is not False
            or own.get('in_player_list') is not True
            or value.get('local_player_entity_uid') != uid
            or value.get('clan') != snapshot.get('clan')):
        return _reject('growth_local_human_identity_unverified')
    times = [value.get(k) for k in ('time_before', 'time_after', 'game_time_seconds')]
    parent_time = snapshot.get('game_time_seconds')
    if (not all(_number(t) and t >= 0 for t in times)
            or not _number(parent_time) or times[1] < times[0]
            or times[1] != times[2] or times[1] - times[0] > 5
            or not -0.025 <= parent_time - times[2] <= 5
            or value.get('read_guard_issues') != []):
        return _reject('growth_clock_or_read_guard_invalid')
    inputs = value.get('native_inputs')
    if type(inputs) is not dict:
        return _reject('growth_native_inputs_invalid')
    native = {}
    for key, kind in NATIVE_KINDS.items():
        if not _valid(inputs.get(key), kind):
            return _reject('growth_native_inputs_invalid')
        native[key] = inputs.get(key)
    owned = snapshot.get('owned_zone_ids')
    buildings = snapshot.get('buildings')
    halls = value.get('town_halls')
    if (type(owned) is not list or any(type(z) is not int for z in owned)
            or type(buildings) is not list or type(halls) is not list or len(halls) > 1000):
        return _reject('growth_town_hall_scope_unverified')
    own_halls = {b.get('uid'): b for b in buildings if type(b) is dict
                 and b.get('kind') == 'TownHall' and b.get('zone_id') in owned}
    output, seen = [], set()
    for row in halls:
        if (type(row) is not dict or type(row.get('uid')) is not int
                or row['uid'] not in own_halls or row['uid'] in seen
                or row.get('zone_id') != own_halls[row['uid']].get('zone_id')):
            return _reject('growth_town_hall_outside_parent_scope')
        seen.add(row['uid'])
        out = {'uid': row['uid'], 'zone_id': row['zone_id']}
        for key, kind in (('growth_progress', 'nonnegative'), ('force_recruit', 'bool'),
                          ('recruit_stack', 'nonnegative'), ('enabled', 'bool')):
            if not _valid(row.get(key), kind):
                return _reject('growth_town_hall_fields_invalid')
            out[key] = row.get(key)
        output.append(out)
    bonus = value.get('bonus_inputs')
    if type(bonus) is not dict:
        return _reject('growth_bonus_inputs_invalid')
    tech = bonus.get('normal_map_tech_presence')
    if tech is not None and (type(tech) is not dict
                            or any(type(tech.get(k)) is not bool for k in GROWTH_TECHS)):
        return _reject('growth_bonus_inputs_invalid')
    bonuses = {'normal_map_tech_presence': {k: tech[k] for k in GROWTH_TECHS} if tech else None}
    for key, kind in (('special_tech_providers_present', 'bool'),
                      ('active_meal_count', 'integer'), ('conquest_bonus_count', 'integer'),
                      ('additional_rule_count', 'integer')):
        if not _valid(bonus.get(key), kind):
            return _reject('growth_bonus_inputs_invalid')
        bonuses[key] = bonus.get(key)
    pop = snapshot.get('population')
    ordinary_mode = _verified_ordinary_mode(snapshot)
    threshold = (55 + 5 * pop if ordinary_mode and snapshot.get('clan') in SUPPORTED_THRESHOLD_CLANS
                 and type(pop) in (int, float) and _number(pop) and pop >= 0 and int(pop) == pop
                 else None)
    model_reason = ('growth_threshold_mode_unverified' if not ordinary_mode else
                    'growth_threshold_clan_unverified' if snapshot.get('clan') not in SUPPORTED_THRESHOLD_CLANS else
                    'growth_threshold_population_unavailable' if threshold is None else None)
    gates = []
    for key, block_value, code in (
            ('growth_disabled_modifier', True, 'growth_disabled_modifier_observed'),
            ('has_conceded', True, 'player_conceded_observed'),
            ('debug_population_can_grow', False, 'population_growth_debug_gate_off_observed')):
        if native[key] is block_value:
            gates.append(code)
    if native['current_happiness'] is not None and native['current_happiness'] <= 0:
        gates.append('cached_happiness_nonpositive_observed')
    cap_initializing = (native['population_cap_cache'] == 0
                        and population_cap_bootstrap_unavailable(snapshot))
    if (not cap_initializing and native['population_cap_cache'] is not None and _number(pop)
            and pop >= native['population_cap_cache']):
        gates.append('population_at_cached_housing_limit_observed')
    for hall in output:
        progress = hall['growth_progress']
        hall['progress_fraction_model'] = (progress / threshold
            if progress is not None and threshold and threshold > 0 else None)
    return {
        'schema_version': 1, 'status': 'partial', 'source': SOURCE,
        'policy_version': 'player-growth.v1', 'scope': 'local_player_own_growth_inputs',
        'hlboot_sha256': HLBOOT_SHA256, 'data_cdb_sha256': DATA_CDB_SHA256,
        'local_player_entity_uid': uid, 'clan': snapshot['clan'],
        'game_time_seconds': times[2], 'time_before': times[0], 'time_after': times[1],
        'sample_span_game_seconds': times[1] - times[0], 'atomic': False,
        'native_inputs': native, 'town_halls': sorted(output, key=lambda h: h['uid']),
        'bonus_inputs': bonuses, 'growth_bonus': None,
        'growth_bonus_source': 'not_a_stored_field_not_evaluated',
        'model': {'next_recruit_threshold': threshold,
                  'threshold_source': 'verified_bytecode_13509_55_plus_5P' if threshold else None,
                  'population_input': pop if _number(pop) else None,
                  'population_input_source': 'parent_housing_population_not_unit_count',
                  'population_cache_matches_parent': native['population_cache'] == pop
                       if native['population_cache'] is not None and _number(pop) else None,
                  'observed_blocking_inputs': gates,
                  'blocking_inputs_complete': False,
                  'formula_eta_game_seconds': None,
                  'formula_eta_status': model_reason or 'complete_growth_bonus_and_spawn_gates_unavailable'},
        'unknown_fields': list(UNKNOWN) + (['model.next_recruit_threshold'] if threshold is None else [])
                          + (['native_inputs.population_cap_cache'] if cap_initializing else []),
        'reason_codes': ['growth_mechanism_partially_observed'] + ([model_reason] if model_reason else [])
                        + (['population_cap_bootstrap_uninitialized'] if cap_initializing else []),
        **{k: snapshot[k] for k in IDENTITIES},
    }


class GrowthProgressTracker:
    """Observe progress slopes; never bridge a reset, pause or population change.

    The slope/bonus estimate is the interval's observed effective value, not the
    native getter or a forecast guarantee. A slope crossing birth/reset is absent.
    Call ``update`` for errors as well so the next valid row starts a new baseline.
    """
    def __init__(self, max_gap_game_seconds=5):
        if not _number(max_gap_game_seconds) or max_gap_game_seconds <= 0:
            raise ValueError('Positive finite max gap required')
        self.max_gap = max_gap_game_seconds
        self._previous = None

    def reset(self):
        self._previous = None

    def update(self, observation):
        growth = observation.get('population_growth') if type(observation) is dict else None
        if (type(observation) is not dict or observation.get('status') != 'ok'
                or type(growth) is not dict or growth.get('status') not in ('ok', 'partial')
                or growth.get('source') != SOURCE
                or type(growth.get('native_inputs')) is not dict
                or type(growth.get('town_halls')) is not list
                or type(growth.get('model')) is not dict
                or growth.get('local_player_entity_uid') != observation.get('local_player_entity_uid')
                or growth.get('clan') != observation.get('clan')
                or not all(type(observation.get(k)) is str and IDENTITY.fullmatch(observation[k])
                           and growth.get(k) == observation[k] for k in IDENTITIES)
                or not _number(growth.get('game_time_seconds'))):
            self.reset()
            return []
        key = tuple(observation[k] for k in IDENTITIES) + (
            growth.get('local_player_entity_uid'), growth.get('clan'))
        when = growth['game_time_seconds']
        previous = self._previous
        self._previous = (key, when, growth)
        if previous is None or previous[0] != key:
            return []
        _, start, old = previous
        dt = when - start
        if dt <= 0 or dt > self.max_gap:
            return []
        old_native, native = old['native_inputs'], growth['native_inputs']
        same_inputs = all(old_native.get(k) == native.get(k) for k in (
            'current_happiness', 'population_cache', 'population_cap_cache',
            'growth_disabled_modifier', 'debug_population_can_grow', 'has_conceded'))
        if (not same_inputs or old.get('bonus_inputs') != growth.get('bonus_inputs')
                or old['model'].get('population_input') != growth['model'].get('population_input')
                or old['model'].get('threshold_source') != growth['model'].get('threshold_source')):
            return []
        before = {h['uid']: h for h in old['town_halls']}
        output = []
        for hall in growth['town_halls']:
            old_hall = before.get(hall['uid'])
            if not old_hall:
                continue
            a, b = old_hall.get('growth_progress'), hall.get('growth_progress')
            if (not _number(a) or not _number(b) or b < a
                    or any(old_hall.get(k) != hall.get(k) for k in
                           ('zone_id', 'enabled', 'force_recruit', 'recruit_stack'))):
                continue
            rate = (b - a) / dt
            if not _number(rate):
                continue
            happy = native.get('current_happiness')
            threshold = growth['model'].get('next_recruit_threshold')
            gates = growth['model'].get('observed_blocking_inputs', [])
            remaining = max(0., threshold - b) if _number(threshold) else None
            effective_bonus = (rate / math.sqrt(happy)
                if _number(happy) and happy > 0 and rate > 0 and not gates else None)
            if effective_bonus is not None and not _number(effective_bonus):
                effective_bonus = None
            # Reaching a threshold is not proof a villager can spawn. Unknown
            # under-attack, spawn, temporary effects and within-interval changes
            # remain explicit even for a positive slope.
            eta = remaining / rate if remaining is not None and rate > 0 and not gates else None
            if eta is not None and not _number(eta):
                eta = None
            output.append({
                'town_hall_uid': hall['uid'], 'source': 'observed_growth_progress_interval',
                'start_game_seconds': start, 'end_game_seconds': when,
                'elapsed_game_seconds': dt, 'progress_before': a, 'progress_after': b,
                'observed_progress_per_game_second': rate,
                'observed_effective_bonus_estimate': effective_bonus,
                'bonus_estimate_semantics': 'interval_slope_divided_by_sqrt_cached_happiness_not_native_getter',
                'conditional_threshold_eta_game_seconds': eta,
                'eta_semantics': 'threshold_only_if_observed_slope_persists_spawn_time_unverified',
                'inputs_observed_unchanged_at_endpoints': True,
                'within_interval_changes_excluded': False,
                'unknown_fields': ['native_growth_bonus', 'actual_next_birth_time',
                                   'growth_changes_between_samples', 'town_hall_under_attack'],
                **{k: observation[k] for k in IDENTITIES},
            })
        return output
