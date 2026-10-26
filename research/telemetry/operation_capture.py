"""Read persistent own-player operation state; never execute a game method.

These are sampled states and stored cost records, not a complete click log or
transaction ledger. Field names and semantics are pinned to the verified build.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import struct
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'runtime'))
from runtime_types import LayoutError
from live_reader import objects, string
from runtime_decode import checked_map_backing, enum_values

VERIFIED_HASH = 'db0459d251879698420f58f6819e0ab2f75a3cde6e65611f2ad723f3b4b7ef76'
VERIFIED_CDB_HASH = '88d4c84b9622ce237533afc8674174aa9006d87a03258cd7e5fd671f930174e3'
IDENTITY_FIELDS = ('session_key', 'capture_run_id', 'capture_segment_id')
RESOURCE_ALLOWLIST = frozenset(('Food', 'Wood', 'Money', 'Stone', 'Iron',
                               'Lore', 'Faith', 'Prescience', 'Dominion'))
COLONIZE_RESOURCES = {'ColWithFood': 'Food', 'ColWithMoney': 'Money',
                      'ColWithLore': 'Lore', 'ColWithFaith': 'Faith',
                      'ColWithPrescience': 'Prescience'}


def _number(value, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise LayoutError('Invalid operation-state scalar')
    if minimum is not None and value < minimum:
        raise LayoutError('Out-of-range operation-state scalar')
    return value


def _integer(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LayoutError('Invalid operation-state identifier/count')
    return value


def _integer_map_pointers(h, address, limit=4096):
    """Checked IntMap entry pointers. Caller must whitelist keys and decode values.

    Layout is the same verified HashLink IntMap storage used by runtime_decode.
    Both header and live storage are re-read to detect observed mutations.
    """
    if not address or h.object_type(address).name != 'haxe.ds.IntMap':
        raise LayoutError('Expected own operation IntMap')
    storage = h.field(address, 'h')
    header = h.read(storage, 64)
    cells, nexts, entries, values, free, head, nbuckets, ncells, nentries, capacity = struct.unpack(
        '<QQQQQiiiii4x', header)
    if not 0 <= nentries <= capacity <= limit or not 0 <= ncells <= limit:
        raise LayoutError('Invalid own operation map counts')
    if not nentries:
        if h.read(storage, 64) != header:
            raise LayoutError('Operation map changed while reading')
        return {}
    if not all((cells, nexts, entries, values, ncells)):
        raise LayoutError('Missing own operation map storage')
    fmt = 'b' if capacity < 128 else 'i'
    width = 1 if fmt == 'b' else 4
    chunks = ((cells, ncells * width), (nexts, capacity * width),
              (entries, capacity * 4), (values, capacity * 8))
    buffers = [h.read(ptr, size) for ptr, size in chunks]
    buckets, links, keys, vals = buffers
    active, output = set(), {}
    for bucket in range(ncells):
        index = struct.unpack_from('<' + fmt, buckets, bucket * width)[0]
        while index >= 0:
            if index >= capacity or index in active:
                raise LayoutError('Invalid own operation map chain')
            active.add(index)
            key = struct.unpack_from('<i', keys, index * 4)[0]
            if key in output:
                raise LayoutError('Duplicate own operation map key')
            output[key] = struct.unpack_from('<Q', vals, index * 8)[0]
            index = struct.unpack_from('<' + fmt, links, index * width)[0]
    if len(active) != nentries or h.read(storage, 64) != header:
        raise LayoutError('Operation map changed while reading')
    if any(h.read(ptr, size) != before for (ptr, size), before in zip(chunks, buffers)):
        raise LayoutError('Operation map values changed while reading')
    return output


def _colonize_cost(h, address):
    info = h.object_type(address)
    if info.kind != 18 or info.name != 'enum:ent.vars.ColonizeType':
        raise LayoutError('Unexpected colonization cost enum')
    kind, params = enum_values(h, address)
    if kind not in COLONIZE_RESOURCES or len(params) != 1:
        # Dominion is a cooldown branch; DebugType has no numeric resource cost.
        # Neither is silently represented as a zero-cost payment.
        raise LayoutError('Unsupported non-numeric colonization cost')
    return [{'resource': COLONIZE_RESOURCES[kind],
             'amount': _number(params[0][1], minimum=0)}]


def _price_map(h, world, field, known_zone_ids):
    proxy = h.field(world, field)
    if not proxy or h.object_type(proxy).name != 'hxbit.MapData':
        raise LayoutError('Missing own colonization price map')
    with checked_map_backing(h, proxy, 'haxe.ds.IntMap') as backing:
        pointers = _integer_map_pointers(h, backing)
        # Unknown keys and values are never decoded into hidden objects.
        result = {}
        for zone_id, value in pointers.items():
            if zone_id in known_zone_ids:
                result[zone_id] = _colonize_cost(h, value)
        if h.field(world, field) != proxy:
            raise LayoutError('Own colonization map reference changed while reading')
    return result


def _building_cost(h, building):
    proxy = h.field(building, 'buildingCost')
    if not proxy:
        return None  # An absent stored cost is unknown, not a zero payment.
    if h.object_type(proxy).name != 'hxbit.ArrayProxyData':
        raise LayoutError('Expected stored building-cost array')
    output = []
    for item in objects(h, proxy, limit=64):
        if h.object_type(item).name != 'hxbit.ObjProxy_Oopt_Null_Bool__qty_Int_res_Data_ResourceKind':
            raise LayoutError('Unexpected stored building-cost item')
        resource = string(h, h.field(item, 'res'))
        if resource not in RESOURCE_ALLOWLIST:
            raise LayoutError('Unsupported building-cost resource')
        output.append({'resource': resource, 'amount': _number(h.field(item, 'qty'), minimum=0)})
    return output


def _simple_own_tech_flags(h, me, state):
    """Exact normal-map hasTech fallback only when special providers are empty.

    A fountain provider can override the normal map, and Owl branches may supply
    technology. If either exists we refuse this deliberately narrow fallback.
    """
    tech = h.field(me, 'tech')
    if not tech or h.object_type(tech).name != 'ent.player.TechComponent':
        raise LayoutError('Own tech component unavailable')
    fountain = h.field(tech, 'allFountainResearches')
    if fountain and objects(h, fountain, limit=4096):
        raise LayoutError('Special own tech providers are not decoded')
    if h.field(me, 'owl'):
        branches = h.field(state, 'branches')
        if branches and objects(h, branches, limit=4096):
            raise LayoutError('Owl tech providers are not decoded')
    proxy = h.field(tech, 'researchedTechs')
    if h.object_type(proxy).name != 'hxbit.MapData':
        raise LayoutError('Own researched-tech map unavailable')
    with checked_map_backing(h, proxy, 'haxe.ds.StringMap') as backing:
        values = h.string_map(backing)
        keys = set(values)
        if (h.string_map(backing) != values or h.field(me, 'tech') != tech
                or h.field(tech, 'researchedTechs') != proxy):
            raise LayoutError('Own researched-tech map changed while reading')
    return {name: name in keys for name in ('TrialFaith', 'PaladinCall')}


def capture_operations(h, me, state, *, known_zone_ids, owned_building_pointers,
                       identity=None, profile=None):
    """Return pointer-free own operation state, with independently unknown parts.

    ``known_zone_ids`` is the caller's own discovered/owned-zone ID whitelist.
    ``owned_building_pointers`` is an iterable of ``(pointer, zone_id)`` pairs.
    Each building's current zone ownership is verified again here. Optional
    component failures produce fixed reason codes, never private exception text.
    """
    result = {'schema_version': 1, 'source': 'native_own_operation_state_read_only',
              'status': 'unknown', 'local_player_entity_uid': None,
              'game_time_seconds': None, 'feast': {}, 'colonizations': [],
              'building_costs': [], 'unknown_fields': [], 'reason_codes': [],
              'atomic': False, 'complete_transaction_ledger': False}
    for key in IDENTITY_FIELDS:
        if identity and key in identity:
            result[key] = identity[key]
    def unknown(field, code):
        result['unknown_fields'].append(field)
        if code not in result['reason_codes']:
            result['reason_codes'].append(code)
    try:
        if profile is None:
            profile = json.loads((HERE.parent / 'runtime/build_profile.json').read_text(encoding='utf8'))
        if profile.get('hlboot_sha256') != VERIFIED_HASH:
            unknown('all', 'unverified_build')
            return result
        if (h.object_type(me).name != 'ent.Player' or h.field(me, 'aiJob')
                or h.field(state, 'me') != me):
            unknown('all', 'unverified_local_human_player')
            return result
        result['local_player_entity_uid'] = _integer(h.field(me, '__uid'))
        result['game_time_seconds'] = _number(h.field(state, 'time'), minimum=0)
        known = {_integer(z) for z in known_zone_ids}
        if len(known) > 4096:
            raise LayoutError('Too many known zones')
        world, res = h.field(me, 'world'), h.field(me, 'res')
        if h.object_type(world).name != 'ent.player.WorldComponent' or h.object_type(res).name != 'ent.player.ResourcesComponent':
            raise LayoutError('Unexpected own operation components')
    except (OSError, ValueError, KeyError, TypeError, struct.error):
        unknown('all', 'own_operation_identity_or_component_unavailable')
        return result
    time_before = result['game_time_seconds']
    feast_fields = {'feast_count': ('feastCount', _integer),
                    'last_feast': ('lastFeast', _number),
                    'free_feast': ('freeFeast', _integer),
                    'free_feast_count': ('freeFeastCount', _integer),
                    'free_feast_time': ('freeFeastTime', _number)}
    for output, (field, validate) in feast_fields.items():
        try:
            result['feast'][output] = validate(h.field(res, field))
        except (OSError, ValueError, KeyError, TypeError):
            result['feast'][output] = None
            unknown('feast.' + output, 'own_feast_field_unavailable')
    try:
        instigator = h.field(res, 'feastInstigator')
        result['feast']['instigator_is_self'] = instigator == me if instigator else None
    except (OSError, ValueError, KeyError, TypeError):
        result['feast']['instigator_is_self'] = None
        unknown('feast.instigator_is_self', 'own_feast_field_unavailable')
    try:
        result['feast']['goat_cost_reduction'] = _number(h.field(me, 'goatFeastCostReduction'))
    except (OSError, ValueError, KeyError, TypeError):
        result['feast']['goat_cost_reduction'] = None
        unknown('feast.goat_cost_reduction', 'own_feast_field_unavailable')
    try:
        team_leader = h.field(me, 'teamLeader')
        result['feast']['team_leader_present'] = bool(team_leader)
        result['feast']['team_leader_is_self'] = not team_leader or team_leader == me
    except (OSError, ValueError, KeyError, TypeError):
        result['feast']['team_leader_present'] = None
        result['feast']['team_leader_is_self'] = None
        unknown('feast.team_leader_is_self', 'own_feast_team_source_unavailable')
    try:
        hunt = h.field(me, 'hunt')
        if not hunt or h.object_type(hunt).name != 'ent.player.ArrowsComponent':
            raise LayoutError('Own feast hunting modifier unavailable')
        result['feast']['hunt_cost_reduction'] = _number(h.field(hunt, 'feastCostReduction'))
    except (OSError, ValueError, KeyError, TypeError):
        result['feast']['hunt_cost_reduction'] = None
        unknown('feast.hunt_cost_reduction', 'own_feast_modifier_unavailable')
    try:
        if profile.get('data_cdb_sha256') != VERIFIED_CDB_HASH:
            raise LayoutError('LivingLegends constants are not verified for this data build')
        pack = h.field(me, 'pack')
        if not pack or h.object_type(pack).name != 'ent.player.PackComponent':
            raise LayoutError('Own feast pack modifier unavailable')
        stacks = _integer(h.field(pack, 'livingLegendStacks'))
        # Verified #14132: min(LivingLegends value ID 0, stacks * value ID 3).
        # This build's data.cdb gives .5 and .1; no game method is executed.
        result['feast']['living_legend_stacks'] = stacks
        result['feast']['living_legend_cost_reduction'] = min(0.5, stacks * 0.1)
    except (OSError, ValueError, KeyError, TypeError):
        result['feast']['living_legend_stacks'] = None
        result['feast']['living_legend_cost_reduction'] = None
        unknown('feast.living_legend_cost_reduction', 'own_feast_modifier_unavailable')
    try:
        flags = _simple_own_tech_flags(h, me, state)
        result['feast']['has_trial_faith'] = flags['TrialFaith']
    except (OSError, ValueError, KeyError, TypeError):
        flags = None
        result['feast']['has_trial_faith'] = None
        unknown('feast.has_trial_faith', 'own_special_tech_state_unavailable')
    prices = {}
    for field, output in (('colonizedPrice', 'paid_cost'), ('extraColonizedPrice', 'extra_paid_cost')):
        try:
            prices[output] = _price_map(h, world, field, known)
        except (OSError, ValueError, KeyError, TypeError, struct.error):
            prices[output] = None
            unknown('colonizations.' + output, 'own_colonization_price_map_unavailable')
    zone_rows = {}
    try:
        for zone in objects(h, h.field(state, 'zones'), limit=4096):
            zone_id = _integer(h.field(zone, 'id'))
            if zone_id not in known:
                continue
            by_self = h.field(zone, 'colonizeBy') == me
            has_cost = any(zone_id in (costs or {}) for costs in prices.values())
            if not by_self and not has_cost:
                continue
            zone_rows[zone_id] = {'zone_id': zone_id,
                'progress': _number(h.field(zone, 'colonize')),
                'started_by_self': by_self,
                'cost_reduction': None}
            try:
                reduction = h.field(zone, 'costReduction')
                zone_rows[zone_id]['cost_reduction'] = _number(h.dynamic(reduction)) if reduction else None
            except (OSError, ValueError, KeyError, TypeError):
                unknown('colonizations.cost_reduction', 'own_colonization_modifier_unavailable')
    except (OSError, ValueError, KeyError, TypeError):
        unknown('colonizations.progress', 'known_zone_colonization_state_unavailable')
    for costs in prices.values():
        for zone_id in costs or {}:
            zone_rows.setdefault(zone_id, {'zone_id':zone_id, 'progress':None,
                                           'started_by_self':None, 'cost_reduction':None})
    for zone_id in sorted(zone_rows):
        row = zone_rows[zone_id]
        for output, values in prices.items():
            # Missing entry means no stored record, never proof of zero cost.
            row[output] = values.get(zone_id) if values is not None else None
            row[output + '_record_status'] = ('unavailable' if values is None else
                'stored_record' if zone_id in values else 'no_record')
        result['colonizations'].append(row)
    building_kinds, buildings_complete = set(), True
    for building, provided_zone_id in owned_building_pointers:
        try:
            zone = h.field(building, 'zone')
            zone_id = _integer(h.field(zone, 'id'))
            if zone_id != provided_zone_id or zone_id not in known or h.field(zone, 'owner') != me:
                unknown('building_costs', 'own_building_ownership_not_verified')
                buildings_complete = False
                continue
            kind = string(h, h.field(building, 'kind'))
            if not isinstance(kind, str) or not 0 < len(kind) <= 128:
                raise LayoutError('Invalid building kind')
            row = {'uid':_integer(h.field(building, '__uid')), 'zone_id':zone_id,
                   'kind':kind, 'was_free':None, 'cost':None, 'cost_record_status':'unknown'}
            try:
                was_free = h.field(building, 'wasFree')
                if not isinstance(was_free, bool):
                    raise LayoutError('Invalid wasFree flag')
                row['was_free'] = was_free
                row['cost'] = _building_cost(h, building)
                row['cost_record_status'] = 'stored_record' if row['cost'] is not None else 'not_stored'
            except (OSError, ValueError, KeyError, TypeError):
                unknown('building_costs.cost', 'own_building_cost_unavailable')
            result['building_costs'].append(row)
            building_kinds.add(kind)
        except (OSError, ValueError, KeyError, TypeError):
            buildings_complete = False
            unknown('building_costs', 'own_building_state_unavailable')
    for kind, field in (('RelicGoat', 'has_relic_goat'), ('RelicChalice', 'has_relic_chalice')):
        # #4939 searches enabled own-zone buildings and may also inspect a
        # Paladin-held relic. With no candidate building and no PaladinCall,
        # false is proven; a present candidate is conservatively unknown here.
        if buildings_complete and flags is not None and not flags['PaladinCall'] and kind not in building_kinds:
            result['feast'][field] = False
        else:
            result['feast'][field] = None
            unknown('feast.' + field, 'own_relic_active_state_not_proven_absent')
    try:
        time_after = _number(h.field(state, 'time'), minimum=0)
        result['time_before'], result['time_after'] = time_before, time_after
        result['sample_span_game_seconds'] = time_after - time_before
        if time_after < time_before:
            unknown('all', 'game_time_rolled_back_during_operation_read')
    except (OSError, ValueError, KeyError, TypeError):
        unknown('time_after', 'operation_end_time_unavailable')
    result['status'] = 'partial' if result['unknown_fields'] else 'ok'
    return result
