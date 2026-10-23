"""Sample native local-player recruitment progress with read-only field reads.

``growth`` is a stored value; ``getGrowthBonus``/``getNoGrowthReason`` are
functions. This module never invokes them and never substitutes a default bonus.
The returned inputs are deliberately narrower than the full growth mechanism.
"""
from __future__ import annotations

import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'runtime'))
from live_reader import objects, string
from runtime_types import LayoutError
from resource_caps import VERIFIED_HASH, VERIFIED_CDB_HASH

SOURCE = 'native_own_growth_inputs_read_only'
IDENTITIES = ('session_key', 'capture_run_id', 'capture_segment_id')
GROWTH_TECHS = ('Recruit', 'EndlessTide', 'PlotThrone')
NATIVE_FIELDS = {
    'resource_growth_progress': ('growth', 'nonnegative'),
    'current_happiness': ('currentHappiness', 'number'),
    'population_cache': ('population', 'integer'),
    'population_cap_cache': ('nbMaxPop', 'integer'),
    'reserve_population_cache': ('nbReservePop', 'integer'),
    'recruit_stack': ('recruitStack', 'nonnegative'),
    'force_recruit': ('forceRecruit', 'bool'),
    'low_housing_timer': ('lowHouse', 'nonnegative'),
    'low_happiness_timer': ('lowHappy', 'nonnegative'),
    'no_population_growth_time': ('noPopGrowthTime', 'nonnegative'),
}


def _validate(value, kind):
    if kind == 'bool':
        if type(value) is not bool:
            raise LayoutError('Invalid native growth boolean')
    elif kind == 'integer':
        if type(value) is not int or not 0 <= value < 2**31:
            raise LayoutError('Invalid native growth count')
    elif (type(value) not in (int, float) or not math.isfinite(value)
          or abs(value) >= 2**53 or (kind == 'nonnegative' and value < 0)):
        raise LayoutError('Invalid native growth scalar')
    return value


def capture_growth(h, me, state, owned_building_pointers=(), *, profile, identity):
    """Return stored progress, happiness, gates and independently missing inputs.

    ``owned_building_pointers`` contains ``(pointer, zone_id)`` pairs, as in
    capture_operations. Ownership and TownHall.player are independently checked.
    No enemy objects, account names, memory addresses, getters or injected code
    enter the output. Build/data hashes are mandatory and mismatches fail closed.
    """
    if (type(profile) is not dict or profile.get('hlboot_sha256') != VERIFIED_HASH
            or profile.get('data_cdb_sha256') != VERIFIED_CDB_HASH):
        raise LayoutError('Growth semantics are not verified for this build/data')
    if (type(identity) is not dict
            or any(type(identity.get(k)) is not str or not identity[k] for k in IDENTITIES)):
        raise LayoutError('Growth capture requires complete capture identity')
    if (h.object_type(me).name != 'ent.Player'
            or h.object_type(state).name != 'GameState'
            or h.field(state, 'me') != me or h.field(me, 'aiJob')):
        raise LayoutError('Growth scope is not the local human player')
    res = h.field(me, 'res')
    if (not res or h.object_type(res).name != 'ent.player.ResourcesComponent'
            or h.field(res, 'entity') != me):
        raise LayoutError('Growth component is not bound to the local player')
    before = _validate(h.field(state, 'time'), 'nonnegative')
    result = {
        'schema_version': 1, 'source': SOURCE, 'status': 'ok', 'atomic': False,
        'hlboot_sha256': VERIFIED_HASH, 'data_cdb_sha256': VERIFIED_CDB_HASH,
        'local_player_entity_uid': _validate(h.field(me, '__uid'), 'integer'),
        'clan': string(h, h.field(me, 'clan')),
        'time_before': before, 'native_inputs': {}, 'town_halls': [],
        'growth_bonus': None, 'growth_bonus_source': 'not_a_stored_field_not_evaluated',
        'bonus_inputs': {}, 'unknown_fields': [], 'reason_codes': [],
        **{k: identity[k] for k in IDENTITIES},
    }
    def unknown(field, code):
        if field not in result['unknown_fields']:
            result['unknown_fields'].append(field)
        if code not in result['reason_codes']:
            result['reason_codes'].append(code)
    def optional(target, key, read, kind):
        try:
            target[key] = _validate(read(), kind)
        except (OSError, ValueError, KeyError, TypeError):
            target[key] = None
            unknown(key, 'growth_optional_native_field_unavailable')
    native = result['native_inputs']
    for output, (field, kind) in NATIVE_FIELDS.items():
        optional(native, output, lambda field=field: h.field(res, field), kind)
    optional(native, 'has_conceded', lambda: h.field(me, 'hasConceded'), 'bool')
    optional(native, 'player_game_over', lambda: h.field(me, 'gameOver'), 'bool')
    optional(native, 'game_finished', lambda: h.field(state, 'gameFinished'), 'bool')
    optional(native, 'debug_population_can_grow',
             lambda: h.field(h.field(me, 'debug'), 'populationCanGrow'), 'bool')
    try:
        flags = h.field(me, 'modifierFlags')
        if h.object_type(flags).name != 'hxbit.EnumFlagsData':
            raise LayoutError('Unexpected own growth flags')
        flag_value = h.field(flags, 'value')
        if type(flag_value) is not int or not -(2**31) <= flag_value < 2**31:
            raise LayoutError('Invalid own growth flags')
        native['growth_disabled_modifier'] = bool(flag_value & 32)
    except (OSError, ValueError, KeyError, TypeError):
        native['growth_disabled_modifier'] = None
        unknown('growth_disabled_modifier', 'growth_optional_native_field_unavailable')
    initial_progress = native['resource_growth_progress']
    watched = []
    seen = set()
    for building, zone_id in owned_building_pointers:
        zone_id = _validate(zone_id, 'integer')
        # Only already supplied own buildings are inspected. A pointer in the
        # supplied list still does not establish ownership on its own.
        zone = h.field(building, 'zone')
        if (h.object_type(zone).name != 'ent.Zone' or h.field(zone, 'id') != zone_id
                or h.field(zone, 'owner') != me):
            raise LayoutError('Own growth building scope changed during capture')
        if h.object_type(building).name != 'ent.b.TownHall':
            continue
        if h.field(building, 'player') != me:
            raise LayoutError('TownHall is not bound to the local player')
        uid = _validate(h.field(building, '__uid'), 'integer')
        if uid in seen:
            raise LayoutError('Duplicate own TownHall')
        seen.add(uid)
        row = {'uid': uid, 'zone_id': zone_id}
        for out, field, kind in (
                ('growth_progress', 'growth', 'nonnegative'),
                ('force_recruit', 'forceRecruit', 'bool'),
                ('recruit_stack', 'recruitStack', 'nonnegative'),
                ('enabled', 'enabled', 'bool')):
            optional(row, out, lambda field=field: h.field(building, field), kind)
        watched.append((building, zone, uid, row['growth_progress']))
        result['town_halls'].append(row)
    result['town_halls'].sort(key=lambda row: row['uid'])
    bonus = result['bonus_inputs']
    # Presence in the normal map is not a complete hasTech result: fountains,
    # borrowed technologies and Owl branches can supply other paths.
    try:
        tech = h.field(me, 'tech')
        if h.object_type(tech).name != 'ent.player.TechComponent':
            raise LayoutError('Unexpected own growth tech component')
        proxy = h.field(tech, 'researchedTechs')
        if h.object_type(proxy).name != 'hxbit.MapData':
            raise LayoutError('Unexpected own growth tech map')
        backing = h.field(proxy, 'map')
        if h.object_type(backing).name != 'haxe.ds.StringMap':
            raise LayoutError('Unexpected own growth tech backing map')
        tech_keys = set(h.string_map(backing))
        bonus['normal_map_tech_presence'] = {key: key in tech_keys for key in GROWTH_TECHS}
        fountain = h.field(tech, 'allFountainResearches')
        bonus['special_tech_providers_present'] = bool(
            fountain and objects(h, fountain, limit=4096)) or bool(h.field(me, 'owl'))
    except (OSError, ValueError, KeyError, TypeError):
        bonus['normal_map_tech_presence'] = None
        bonus['special_tech_providers_present'] = None
        unknown('bonus_inputs.normal_map_tech_presence', 'growth_bonus_input_unavailable')
    for key, address in (('active_meal_count', None), ('conquest_bonus_count', None),
                         ('additional_rule_count', None)):
        try:
            if key == 'active_meal_count':
                address = h.field(h.field(me, 'cook'), 'activeMeals')
            elif key == 'conquest_bonus_count':
                address = h.field(me, 'bonuses')
            else:
                address = h.field(state, 'additionalRules')
            bonus[key] = len(objects(h, address, limit=4096)) if address else 0
        except (OSError, ValueError, KeyError, TypeError):
            bonus[key] = None
            unknown('bonus_inputs.' + key, 'growth_bonus_input_unavailable')
    # Do not claim all gates or effects have been decoded. Zone.isUnderAttack,
    # getHappiness and getGrowthBonus are methods and remain uninvoked.
    for field in ('growth_bonus', 'current_computed_happiness',
                  'town_hall_under_attack', 'complete_growth_block_reason',
                  'birth_spawn_eligibility', 'all_growth_modifier_values'):
        unknown(field, 'growth_getter_result_not_evaluated')
    after = _validate(h.field(state, 'time'), 'nonnegative')
    races = []
    if (h.field(state, 'me') != me or h.field(me, 'res') != res
            or h.field(res, 'entity') != me):
        raise LayoutError('Local growth identity changed during capture')
    if (initial_progress is not None
            and h.field(res, 'growth') != initial_progress):
        races.append('resource_growth_changed_during_read')
    for building, zone, uid, progress in watched:
        if (h.field(building, 'zone') != zone or h.field(zone, 'owner') != me
                or h.field(building, 'player') != me or h.field(building, '__uid') != uid):
            raise LayoutError('TownHall scope changed during capture')
        if progress is not None and h.field(building, 'growth') != progress:
            races.append('town_hall_growth_changed_during_read')
    if after < before:
        races.append('game_time_rolled_back_during_read')
    result.update(game_time_seconds=after, time_after=after,
                  sample_span_game_seconds=after - before,
                  read_guard_issues=sorted(set(races)))
    result['status'] = 'read_race' if races else 'partial'
    return result
