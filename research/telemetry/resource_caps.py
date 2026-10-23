"""Build-checked, read-only interpretation of ordinary resource stock limits.

No game function is called. Dynamic values and constants are read through
HashLink metadata; unresolved special-mode branches remain explicitly unknown.
Evidence: getMax #13505, Food add closure #38211, regular cycle #13460.
"""
from __future__ import annotations

import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from runtime_types import LayoutError

VERIFIED_HASH = 'db0459d251879698420f58f6819e0ab2f75a3cde6e65611f2ad723f3b4b7ef76'
VERIFIED_CDB_HASH = '88d4c84b9622ce237533afc8674174aa9006d87a03258cd7e5fd671f930174e3'
CONST_TYPE_INDEX = 46856
CONST_NAMES = ('BaseFoodStock', 'FoodStockIncrease', 'FoodUpgradedStockIncrease')
SUPPORTED_CLANS = frozenset(('Bear', 'Raven'))
RESOURCES = ('Food', 'Wood', 'Money', 'Stone', 'Iron', 'Lore')
INTEGRATION_ORDER = 'consumption_then_production'


def _profile(profile):
    if (profile.get('hlboot_sha256') != VERIFIED_HASH
            or profile.get('data_cdb_sha256') != VERIFIED_CDB_HASH):
        raise LayoutError('Resource cap semantics are not verified for this build/data')


def _number(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)):
        raise LayoutError('Invalid resource cap scalar')
    return float(value)


def _string(h, address):
    if not address or h.object_type(address).name != 'String':
        raise LayoutError('Expected cap context String')
    size = h.field(address, 'length')
    if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= 128:
        raise LayoutError('Invalid cap context String length')
    return h.read(h.field(address, 'bytes'), size * 2).decode('utf-16-le')


def _objects(h, address, limit=10000):
    for _ in range(5):
        name = h.object_type(address).name
        if name == 'hl.types.ArrayObj':
            size = h.field(address, 'length')
            if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= limit:
                raise LayoutError('Invalid cap context array size')
            array = h.native_array(h.field(address, 'array'), limit=limit * 2)
            if size > array['size']:
                raise LayoutError('Cap context array exceeds capacity')
            return array['values'][:size]
        if name in ('hxbit.ArrayProxyData', 'hl.types.ArrayDyn'):
            address = h.field(address, 'array')
            continue
        raise LayoutError('Unexpected cap context array wrapper')
    raise LayoutError('Cap context array nesting exceeded')


def _virtual_int(h, address, wanted):
    """Read one materialized virtual slot, never a dynamic getter."""
    info = h.object_type(address)
    if info.kind != 15:
        raise LayoutError('Expected materialized BuildingInf virtual')
    fields = h.ptr(info.parameter)
    count = h.unpack(info.parameter + 8, 'i')
    if not 0 <= count <= 512:
        raise LayoutError('Invalid virtual field count')
    for index in range(count):
        field = fields + index * 24
        if h.utf16(h.ptr(field)) != wanted:
            continue
        tp = h.ptr(field + 8)
        if h.type_info(tp).kind != 3:
            raise LayoutError('Expected integer cap context virtual field')
        slot = h.ptr(address + 24 + index * 8)
        if not slot:
            raise LayoutError('Required cap context virtual slot unavailable')
        return h.value_at(slot, tp)
    raise LayoutError('Required cap context virtual field absent')


def read_native_constants(h, type_base, *, profile):
    """Return the three live $Const values via this build's class metadata."""
    _profile(profile)
    info = h.type_info(type_base + CONST_TYPE_INDEX * 32)
    if info.name != 'Const':
        raise LayoutError('Const type array mismatch')
    static = h.ptr(h.ptr(info.parameter + 56))
    if not static or h.object_type(static).name != '$Const':
        raise LayoutError('Static Const class mismatch')
    values = {name: _number(h.field(static, name)) for name in CONST_NAMES}
    if not all(0 < value < 1_000_000 for value in values.values()):
        raise LayoutError('Implausible native Food stock constant')
    return values


def _unknown(reason):
    return {'stock_cap': None, 'stock_cap_verified': False,
            'stock_cap_source': 'unknown', 'stock_cap_status': reason,
            'integration_order': INTEGRATION_ORDER}


def derive_standard_caps(*, clan, bifrost_present, conquest_bonus_count,
                         buildings, constants, profile, ordinary_singleplayer_ffa=False):
    """Pure formula for observed Bear/Raven state, with explicit prerequisites.

    ``buildings`` must cover all owned player zones. Each FoodSilo needs its
    native ``upgraded`` integer and ``force_upgraded`` flag (BuildingInf.flags
    bit 22). A construction targeting FoodSilo does not count. The live
    adapter below enforces scope and ownership. Historical callers must
    independently validate the same scope before passing these inputs.
    """
    _profile(profile)
    resources = {name: _unknown('not_implemented') for name in RESOURCES}
    reason = None
    if clan not in SUPPORTED_CLANS:
        reason = 'unsupported_clan_branch'
    elif bifrost_present is not False:
        reason = 'unsupported_or_unknown_bifrost_branch'
    elif type(conquest_bonus_count) is not int or conquest_bonus_count != 0:
        reason = 'conquest_bonus_branch_not_decoded'
    if reason:
        resources['Food'] = _unknown(reason)
        return {'schema_version': 1, 'status': 'unsupported_context', 'resources': resources}
    constants = {name: _number(constants[name]) for name in CONST_NAMES}
    if not all(0 < x < 1_000_000 for x in constants.values()):
        raise LayoutError('Implausible Food stock constant')
    components = []
    seen = set()
    total = constants['BaseFoodStock']
    for building in buildings:
        if building.get('kind') != 'FoodSilo':
            continue
        uid = building.get('uid')
        if not isinstance(uid, int) or isinstance(uid, bool) or uid in seen:
            raise LayoutError('Duplicate or invalid FoodSilo identity')
        seen.add(uid)
        upgrade = building.get('upgraded')
        force = building.get('force_upgraded')
        if isinstance(upgrade, bool) or not isinstance(upgrade, int) or upgrade < 0 or not isinstance(force, bool):
            raise LayoutError('Unresolved FoodSilo upgrade state')
        is_upgraded = upgrade > 0 or force
        increment = constants['FoodUpgradedStockIncrease' if is_upgraded else 'FoodStockIncrease']
        total += increment
        components.append({'building_uid': uid, 'zone_id': building.get('zone_id'),
                           'is_upgraded': is_upgraded, 'capacity_added': increment})
    # Native getMax returns OToInt; all terms are positive in this branch.
    resources['Food'] = {
        'stock_cap': int(total), 'stock_cap_verified': True,
        'stock_cap_source': 'verified_native_getMax_formula',
        'stock_cap_status': 'finite_limit_verified',
        'integration_order': INTEGRATION_ORDER,
        'hlboot_sha256': VERIFIED_HASH, 'data_cdb_sha256': VERIFIED_CDB_HASH,
        'components': {'base': constants['BaseFoodStock'], 'silos': components,
                       'silo_multiplier': 1.0},
        'evidence_functions': [13505, 5821, 38211, 13460, 13495],
    }
    if ordinary_singleplayer_ffa is True:
        for name in RESOURCES[1:]:
            resources[name] = {
                'resource': name, 'stock_cap': None, 'stock_cap_verified': True,
                'stock_cap_source': 'verified_native_no_finite_limit',
                'stock_cap_status': 'no_finite_limit_verified',
                'integration_order': None,
                'hlboot_sha256': VERIFIED_HASH, 'data_cdb_sha256': VERIFIED_CDB_HASH,
                'scope': 'ordinary_bear_raven_singleplayer_ffa',
                'evidence_functions': ([13505, 13495, 13503] if name == 'Wood' else
                                       [13505, 13495, 13502] if name == 'Money' else
                                       [13505, 13495, 38212, 13485, 13843, 16591]),
            }
    return {'schema_version': 1, 'status': 'ok', 'resources': resources}


def _ordinary_singleplayer_ffa(h, state):
    """Verify the narrow supported mode without invoking native predicates."""
    try:
        from .runtime_decode import enum_values
    except ImportError:
        from runtime_decode import enum_values
    if h.field(state, 'editor'):
        return False
    mode = h.field(state, 'mode')
    if not mode or h.object_type(mode).name != 'enum:PlayMode':
        return False
    name, args = enum_values(h, mode)
    if name != 'Singleplayer' or len(args) != 1:
        return False
    tp, multi = args[0]
    if (h.type_info(tp).name != 'enum:MultiMode' or not multi
            or h.object_type(multi).name != 'enum:MultiMode'):
        return False
    name, args = enum_values(h, multi)
    return name == 'FreeForAll' and not args


def capture_resource_caps(h, res, state, *, type_base, profile):
    """Read the Food cap and supported no-finite-limit semantics, without pointers."""
    _profile(profile)
    if h.object_type(res).name != 'ent.player.ResourcesComponent':
        raise LayoutError('Expected ResourcesComponent for cap capture')
    me = h.field(res, 'entity')
    if (not me or h.object_type(me).name != 'ent.Player'
            or h.field(state, 'me') != me):
        raise LayoutError('Resource cap scope is not the local player')
    clan = _string(h, h.field(me, 'clan'))
    bifrost = bool(h.field(state, 'bifrostManager')) or bool(h.field(state, 'bifrostData'))
    bonuses = h.field(me, 'bonuses')
    bonus_count = len(_objects(h, bonuses)) if bonuses else 0
    if clan not in SUPPORTED_CLANS or bifrost or bonus_count:
        return derive_standard_caps(clan=clan, bifrost_present=bifrost,
                                    conquest_bonus_count=bonus_count,
                                    buildings=[], constants={}, profile=profile)
    buildings = []
    seen_zones = set()
    for zone in _objects(h, h.field(me, 'zones')):
        zid = h.field(zone, 'id')
        if zid in seen_zones or h.field(zone, 'owner') != me:
            raise LayoutError('Owned zone array changed during cap capture')
        seen_zones.add(zid)
        for building in _objects(h, h.field(zone, 'buildings')):
            if _string(h, h.field(building, 'kind')) != 'FoodSilo':
                continue
            buildings.append({'uid': h.field(building, '__uid'), 'kind': 'FoodSilo',
                              'zone_id': zid, 'upgraded': h.field(building, 'upgraded'),
                              'force_upgraded': bool(_virtual_int(h, h.field(building, 'inf'), 'flags') & (1 << 22))})
    constants = read_native_constants(h, type_base, profile=profile)
    return derive_standard_caps(clan=clan, bifrost_present=bifrost,
                                conquest_bonus_count=bonus_count,
                                buildings=buildings, constants=constants, profile=profile,
                                ordinary_singleplayer_ffa=_ordinary_singleplayer_ffa(h, state))


def standard_cycle(*, stock, gross, consumption, dt, cap=None):
    """Native ordinary non-shared cycle: consumption, then clipped production.

    This intentionally refuses above-cap starting stock and negative rates;
    special modes, events, rate changes and multiple cycles need other guards.
    No lower-stock clamp is introduced: addResource's ordinary Food path does
    not clamp negatives. ``positive_counter_delta`` is clipped received stock,
    while ``potential_production`` includes overflow that never reached stock.
    """
    stock, gross, consumption, dt = map(_number, (stock, gross, consumption, dt))
    if gross < 0 or consumption < 0 or dt < 0:
        raise LayoutError('Unsupported negative rate or duration')
    if cap is not None:
        cap = _number(cap)
        if cap <= 0 or stock > cap:
            raise LayoutError('Unsupported nonpositive cap or above-cap stock')
    consumed = consumption * dt
    potential = gross * dt
    after_consumption = stock - consumed
    received = min(potential, cap - after_consumption) if cap is not None else potential
    return {'stock_after': after_consumption + received,
            'stock_delta': received - consumed,
            'consumption': consumed, 'potential_production': potential,
            'received_production': received, 'positive_counter_delta': received,
            'overflow': potential - received,
            'integration_order': INTEGRATION_ORDER}
