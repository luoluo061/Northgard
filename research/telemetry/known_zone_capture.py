"""Read supported natural details of this player's already-discovered zones.

The ordinary singleplayer FFA branch is reconstructed from the pinned build;
no native getter is called. Nature classification is deliberately separate
from complete visibility, effective yield, hostility or colonization legality.
Other players' unit owners are compared as pointers and never dereferenced.
"""
from __future__ import annotations

import math
from pathlib import Path
import struct
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'runtime'))
sys.path.insert(0, str(HERE.parents[1] / 'companion/core'))
from runtime_types import LayoutError
from runtime_decode import checked_map_backing
from map_capture import _objects, _string, _materialized
from match_context import _mode, _game_type
from known_zone_details import (SOURCE, SCOPE, HLBOOT_SHA256, DATA_CDB_SHA256,
                                UNKNOWNS, KINDS, MINERALS, CREATURES)

IDENTITIES = ('session_key', 'capture_run_id', 'capture_segment_id')
ERRORS = (ValueError, OSError, KeyError, TypeError, struct.error, OverflowError)
STATE_TYPE = 'hxbit.ObjProxy_12d27ce8ff826a9893dc8cfa55f3e4a4'
# Factory #12534 and the pinned type/proto tables: these classes do not
# override the audited BaseUnit visibility methods, except Shoal.isEnabled.
NATURAL_TYPES = {
    'RuneStone': frozenset(('ent.b.LoreStone',)),
    # RuneStone's aliasKinds group includes CircleStone. Factory #12534 tests
    # LoreStone before CircleStone and therefore normally constructs LoreStone
    # for this kind. Both audited classes inherit the same visibility methods.
    'CircleStone': frozenset(('ent.b.LoreStone', 'ent.b.CircleStone')),
    'Shoal': frozenset(('ent.b.Shoal',)),
    **{kind: frozenset(('ent.Building',)) for kind in
       ('Fertility', 'Stones', 'IronDeposit', 'Grove', 'DeerSpawner')},
}
NATURAL_FLAGS = {'RuneStone': 0, 'CircleStone': 2048, 'Fertility': 0,
                 'Stones': 2056, 'IronDeposit': 2056, 'Shoal': 0,
                 'Grove': 0, 'DeerSpawner': 0}


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value < 2**53:
        raise LayoutError('Invalid known-zone numeric input')
    return value


def _integer(value, *, positive=False):
    if type(value) is not int or not (1 if positive else 0) <= value < 2**31:
        raise LayoutError('Invalid known-zone identifier')
    return value


def _bool(value):
    if type(value) is not bool:
        raise LayoutError('Invalid known-zone boolean')
    return value


def _inherits(h, pointer, name):
    """Check the actual pinned inheritance chain, rather than a name prefix."""
    info = h.object_type(pointer)
    seen = set()
    for _ in range(32):
        if info.name == name:
            return True
        parent = getattr(info, 'super_type', 0)
        if not parent or parent in seen:
            return False
        seen.add(parent)
        info = h.type_info(parent)
    raise LayoutError('Known-zone type inheritance exceeded')


class _Watch:
    """Re-read only the fields/slots used by this component, without writes."""
    def __init__(self, h):
        self.h = h
        self.checks = []

    def custom(self, read):
        value = read()
        self.checks.append((read, value))
        return value

    def field(self, pointer, name):
        return self.custom(lambda: self.h.field(pointer, name))

    def text(self, pointer):
        return self.custom(lambda: _string(self.h, pointer))

    def array(self, pointer, limit):
        value = self.custom(lambda: tuple(_objects(self.h, pointer, limit=limit)))
        if len(value) != len(set(value)):
            raise LayoutError('Duplicate known-zone array member')
        return value

    def virtual(self, pointer, name, kind):
        return self.custom(lambda: _materialized(self.h, pointer, name, kind))

    def boxed(self, pointer, kind):
        if not pointer:
            return None
        if type(pointer) is not int or self.h.object_type(pointer).kind != kind:
            raise LayoutError('Unexpected boxed known-zone scalar')
        value = self.custom(lambda: self.h.dynamic(pointer))
        return _bool(value) if kind == 7 else _number(value)

    def verify(self):
        for read, expected in self.checks:
            if read() != expected:
                raise LayoutError('Known-zone data changed while reading')


def _target_affect_end(h, unit, key):
    """Resolve one effect key; never unbox other map values or call getters.

    Storage layout follows HLReader.string_map (HashLink maps.h). Live chains,
    keys and backing bytes are checked twice. Only the exact requested key's
    nullable scalar payload is read. Missing evars matches hasAffect=false.
    """
    evars = h.field(unit, 'evars')
    if not evars:
        return None
    if (h.object_type(evars).name != 'ent.EntityVariables'
            or h.field(evars, 'entity') != unit):
        raise LayoutError('Known wildlife effect component identity mismatch')
    proxy = h.field(evars, 'affectsTime')
    with checked_map_backing(h, proxy, 'haxe.ds.StringMap') as backing:
        storage = h.field(backing, 'h')
        header = h.read(storage, 64)
        if len(header) != 64:
            raise LayoutError('Short known wildlife effect-map header')
        cells, nexts, entries, values, free, head, buckets, ncells, count, capacity = struct.unpack(
            '<QQQQQiiiii4x', header)
        if not 0 <= count <= capacity <= 1024 or not 0 <= ncells <= 1024:
            raise LayoutError('Invalid known wildlife effect-map counts')
        result = None
        if count:
            if not cells or not nexts or not values or not ncells:
                raise LayoutError('Invalid known wildlife effect-map storage')
            fmt, width = ('b', 1) if capacity < 128 else ('i', 4)
            cell_data = h.read(cells, ncells * width)
            next_data = h.read(nexts, capacity * width)
            value_data = h.read(values, capacity * 16)
            if (len(cell_data) != ncells * width or len(next_data) != capacity * width
                    or len(value_data) != capacity * 16):
                raise LayoutError('Short known wildlife effect-map storage')
            active, keys, key_reads = set(), set(), []
            for bucket in range(ncells):
                index = struct.unpack_from('<' + fmt, cell_data, bucket * width)[0]
                while index >= 0:
                    if index >= capacity or index in active:
                        raise LayoutError('Invalid known wildlife effect-map chain')
                    active.add(index)
                    key_ptr, value_ptr = struct.unpack_from('<QQ', value_data, index * 16)
                    name = h.utf16(key_ptr)
                    if name in keys:
                        raise LayoutError('Duplicate known wildlife effect key')
                    keys.add(name); key_reads.append((key_ptr, name))
                    if name == key and value_ptr:
                        if h.object_type(value_ptr).kind != 6:
                            raise LayoutError('Unexpected known wildlife effect scalar')
                        value = h.dynamic(value_ptr)
                        if type(value) not in (int, float) or not math.isfinite(value):
                            raise LayoutError('Invalid known wildlife effect expiry')
                        result = value
                    index = struct.unpack_from('<' + fmt, next_data, index * width)[0]
            if len(active) != count:
                raise LayoutError('Known wildlife effect-map count mismatch')
            if (h.read(cells, ncells * width) != cell_data
                    or h.read(nexts, capacity * width) != next_data
                    or h.read(values, capacity * 16) != value_data
                    or any(h.utf16(p) != name for p, name in key_reads)):
                raise LayoutError('Known wildlife effect-map changed')
        if (h.read(storage, 64) != header or h.field(unit, 'evars') != evars
                or h.field(evars, 'entity') != unit or h.field(evars, 'affectsTime') != proxy):
            raise LayoutError('Known wildlife effect roots changed')
        return result


def _headers(watch, zone, game):
    """Classify only headers of current known-zone buildings, including grass."""
    h = watch.h
    array = watch.field(zone, 'buildings')
    refs = watch.array(array, 4096)
    output = []
    for pointer in refs:
        if not _inherits(h, pointer, 'ent.Building'):
            raise LayoutError('Unexpected known-zone building class')
        if watch.field(pointer, 'zone') != zone or watch.field(pointer, 'game') != game:
            raise LayoutError('Stale known-zone building')
        kind = watch.text(watch.field(pointer, 'kind'))
        output.append((pointer, kind))
    return output, any(k == 'TallGrass' for _, k in output), any(k == 'HidingGrass' for _, k in output)


def _base_visibility(watch, pointer, *, has_grass):
    """Audited ordinary discovered branch only; no owner/torch/fog traversal."""
    if not _bool(watch.field(pointer, 'enabled')):
        return False, 'known_zone_visibility_unsupported'
    script = watch.boxed(watch.field(pointer, 'scriptVisible'), 7)
    if script is not None:
        return script, None if script else 'known_zone_visibility_unsupported'
    if has_grass:
        return None, 'known_zone_grass_visibility_unsupported'
    return True, None


def _resources(h, zone, game):
    watch = _Watch(h)
    headers, tall, hiding = _headers(watch, zone, game)
    groups, reasons = {}, set()
    for pointer, kind in headers:
        if kind not in KINDS:
            continue
        if h.object_type(pointer).name not in NATURAL_TYPES[kind]:
            reasons.add('known_zone_resource_type_unsupported')
            continue
        # These minimal identities are kept only in the read guard, not output.
        uid = _integer(watch.field(pointer, '__uid'), positive=True)
        inf = watch.field(pointer, 'inf')
        if (watch.text(watch.virtual(inf, 'id', 11)) != kind
                or watch.virtual(inf, 'flags', 3) != NATURAL_FLAGS[kind]):
            raise LayoutError('Natural resource configuration identity mismatch')
        # Shoal overrides isEnabled (#21797). Its materialized seaShoal cache
        # (#21796) is safe to read, but unresolved/sea branches require a future
        # clan/geometry adapter. Do not report these as ordinary lake fish.
        if kind == 'Shoal' and _bool(watch.field(pointer, 'enabled')):
            sea = watch.boxed(watch.field(pointer, 'seaShoal'), 7)
            if sea is not False:
                reasons.add('known_zone_shoal_enablement_unsupported')
                continue
        visible, reason = _base_visibility(watch, pointer, has_grass=tall or hiding)
        if visible is not True:
            reasons.add(reason or 'known_zone_resource_visibility_unsupported')
            continue
        group = groups.setdefault(kind, {'kind': kind, 'object_count': 0, 'deposits': [],
                                        'remaining_total_raw': None,
                                        'modeled_ui_remaining_total': None})
        group['object_count'] += 1
        if kind in MINERALS:
            state = watch.field(pointer, 'state')
            amount = progress = None
            if state:
                if h.object_type(state).name != STATE_TYPE:
                    raise LayoutError('Unexpected natural-deposit state proxy')
                amount = watch.boxed(watch.field(state, 'contains'), 6)
                progress = watch.boxed(watch.field(state, 'progress'), 6)
            if amount is None or progress is None:
                reasons.add('known_zone_resource_state_missing')
            group['deposits'].append({'natural_object_uid': uid,
                                     'remaining_contains_raw': amount,
                                     'next_extraction_progress_raw': progress})
    watch.verify()
    status = 'partial' if reasons else 'ok'
    if status == 'ok':
        for kind, group in groups.items():
            if kind in MINERALS:
                total = sum(item['remaining_contains_raw'] for item in group['deposits'])
                _number(total)
                group['remaining_total_raw'] = total
                group['modeled_ui_remaining_total'] = math.ceil(total)
    return {'resources_status': status, 'resources': [groups[k] for k in sorted(groups)],
            'resource_reason_codes': sorted(reasons)}, (tall, hiding)


def _wildlife(h, zone, game, state):
    watch = _Watch(h)
    # Independent of resource state decoding: a missing contains field cannot
    # erase a readable no-grass proof and the wildlife component.
    try:
        _, tall, hiding = _headers(watch, zone, game)
        attributes = (tall, hiding)
    except ERRORS:
        watch = _Watch(h)
        tall = hiding = None
        attributes = None
    reasons = set()
    if attributes is None:
        reasons.add('known_zone_natural_attributes_unavailable')
    elif tall or hiding:
        reasons.add('known_zone_grass_visibility_unsupported')
    refs = watch.array(watch.field(zone, 'units'), 4096)
    counts = {kind: 0 for kind in CREATURES}
    expiries = []
    before = _number(h.field(state, 'time'))
    for pointer in refs:
        if not _inherits(h, pointer, 'ent.Unit'):
            raise LayoutError('Unexpected known-zone unit class')
        # Do not read kinds, professions, positions or any owner object for
        # player-owned units (including Kaija), even in a discovered zone.
        if watch.field(pointer, 'owner'):
            continue
        if watch.field(pointer, 'zone') != zone or watch.field(pointer, 'game') != game:
            raise LayoutError('Stale known-zone natural unit')
        kind = watch.text(watch.field(pointer, 'kind'))
        if kind not in CREATURES:
            continue
        if h.object_type(pointer).name not in ('ent.Unit', 'ent.Foe'):
            reasons.add('known_zone_wildlife_type_unsupported')
            continue
        _integer(watch.field(pointer, '__uid'), positive=True)
        inf = watch.field(pointer, 'inf')
        if (watch.text(watch.virtual(inf, 'id', 11)) != kind
                or watch.virtual(inf, 'group', 3) != 36):
            raise LayoutError('Wildlife configuration identity mismatch')
        expiry = watch.custom(lambda p=pointer: _target_affect_end(h, p, 'GullveigCocoon'))
        if expiry is not None:
            expiries.append(expiry)
        # #3021 op11-15 compares time < #3024's raw stored end directly.
        # Neither getter normalizes -1/0 into infinity. Captured time is
        # nonnegative; negative/zero and exactly-equal ends are inactive here.
        if expiry is not None and expiry > before:
            reasons.add('known_zone_wildlife_visibility_unsupported')
            continue
        statuses = watch.array(watch.field(pointer, 'statusList'), 1024)
        if any(watch.text(status) == 'Garrisoned' for status in statuses):
            reasons.add('known_zone_wildlife_visibility_unsupported')
            continue
        visible, reason = _base_visibility(watch, pointer,
            has_grass=attributes is None or tall or hiding)
        if visible is not True:
            reasons.add(reason or 'known_zone_wildlife_visibility_unsupported')
            continue
        counts[kind] += 1
    watch.verify()
    after = _number(h.field(state, 'time'))
    if after < before or any(before < expiry <= after for expiry in expiries):
        raise LayoutError('Wildlife visibility changed at sampling boundary')
    status = 'partial' if reasons else 'ok'
    return {'wildlife_status': status,
            'wildlife': [{'kind': kind, 'count': counts[kind]} for kind in sorted(counts)
                         if status == 'ok' or counts[kind] > 0],
            'wildlife_reason_codes': sorted(reasons)}, attributes


def _context(h, me, state, supplied):
    mode = _mode(h, state)
    game_type = _game_type(h, state)
    editor, bifrost = h.field(state, 'editor'), h.field(state, 'bifrostData')
    if (mode != ('Singleplayer', 'FreeForAll') or game_type != 'Standard'
            or editor or bifrost):
        return False
    # Caller labels are a cross-check only; the native enums above determine
    # support. A fabricated Singleplayer label cannot enable multiplayer.
    if (type(supplied) is not dict
            or supplied.get('mode') != {'kind': 'Singleplayer', 'args': [{'kind': 'FreeForAll', 'args': []}]}
            or supplied.get('game_type') != {'kind': 'Standard', 'args': []}
            or supplied.get('editor_present') is not False
            or supplied.get('bifrost_data_present') is not False):
        return False
    return True


def capture_known_zone_details(h, me, state, *, known_zone_ids, identity, profile, game_context):
    """Optional current-discovered resource and supported wildlife component.

    An unsupported context returns no details. Identity/root/read races raise
    LayoutError for the caller's optional wrapper. Individual resource/wildlife
    failures remain isolated within each known-zone row. Never caches a prior
    map to fill a failed or missing reading.
    """
    if (type(profile) is not dict or profile.get('hlboot_sha256') != HLBOOT_SHA256
            or profile.get('data_cdb_sha256') != DATA_CDB_SHA256):
        raise LayoutError('Unsupported known-zone build/data')
    if type(identity) is not dict or any(type(identity.get(k)) is not str or not identity[k] for k in IDENTITIES):
        raise LayoutError('Known-zone capture identity missing')
    if (h.object_type(state).name != 'GameState' or h.object_type(me).name != 'ent.Player'
            or h.field(state, 'me') != me or h.field(me, 'aiJob')):
        raise LayoutError('Known-zone local human identity invalid')
    root = _Watch(h)
    game = root.field(state, 'game')
    if (not game or h.object_type(game).name != 'Game' or root.field(game, 'state') != state):
        raise LayoutError('Known-zone game identity invalid')
    root.field(state, 'me'); root.field(me, 'aiJob')
    uid = _integer(root.field(me, '__uid'), positive=True)
    players = root.array(root.field(state, 'players'), 64)
    if not players or me not in players:
        raise LayoutError('Known-zone local player is not a current participant')
    before = _number(h.field(state, 'time'))
    root.field(state, 'mode'); root.field(state, 'gameType')
    root.field(state, 'editor'); root.field(state, 'bifrostData')
    supported = root.custom(lambda: _context(h, me, state, game_context))
    if not supported:
        root.verify()
        return {'schema_version': 1, 'status': 'unsupported_context', 'source': SOURCE,
                'scope': 'no_known_zone_details', 'reason_codes': ['known_zone_context_unsupported'],
                'unknown_fields': ['known_zone_details'], 'zones': []}
    if _bool(root.field(game, 'initDone')) is not True:
        raise LayoutError('Known-zone game is not initialized')
    world = root.field(me, 'world')
    if not world or h.object_type(world).name != 'ent.player.WorldComponent':
        raise LayoutError('Known-zone local world unavailable')
    discovered_pointer = root.field(world, 'discoveredIds')
    discovered = root.custom(lambda: tuple(h.int_array(discovered_pointer)))
    if len(discovered) != len(set(discovered)):
        raise LayoutError('Duplicate native discovered ID')
    wanted = [_integer(value) for value in known_zone_ids]
    if len(wanted) != len(set(wanted)) or set(wanted) != set(discovered):
        raise LayoutError('Known-zone request is not the current complete discovered set')
    zone_refs = root.array(root.field(state, 'zones'), 10000)
    known = {}
    for zone in zone_refs:
        if h.object_type(zone).name != 'ent.Zone':
            raise LayoutError('Unexpected current known-zone class')
        zid = _integer(root.field(zone, 'id'))
        if zid not in wanted:
            continue
        if zid in known:
            raise LayoutError('Duplicate current known Zone ID')
        if root.field(zone, 'game') != game:
            raise LayoutError('Known Zone belongs to a different game')
        known[zid] = zone
    if set(known) != set(wanted):
        raise LayoutError('Current known Zone resolution incomplete')
    zones = []
    for zid, zone in sorted(known.items()):
        result = {'zone_id': zid, 'resources_status': 'unavailable', 'wildlife_status': 'unavailable',
                  'natural_attributes_status': 'unavailable', 'tall_grass_present': None,
                  'hiding_grass_present': None, 'resources': [], 'wildlife': [],
                  'resource_reason_codes': [], 'wildlife_reason_codes': []}
        attributes = None
        try:
            resource_result, attributes = _resources(h, zone, game)
            result.update(resource_result)
        except ERRORS:
            result['resource_reason_codes'] = ['known_zone_resource_read_failed']
        try:
            wildlife_result, wildlife_attributes = _wildlife(h, zone, game, state)
            result.update(wildlife_result)
            if wildlife_attributes is not None:
                attributes = wildlife_attributes
        except ERRORS:
            result['wildlife_reason_codes'] = ['known_zone_wildlife_read_failed']
        if attributes is not None:
            result.update(natural_attributes_status='complete', tall_grass_present=attributes[0],
                          hiding_grass_present=attributes[1])
        zones.append(result)
    root.verify()
    deposit_uids = [deposit['natural_object_uid'] for zone in zones
                    for resource in zone['resources'] for deposit in resource['deposits']]
    if len(deposit_uids) != len(set(deposit_uids)):
        raise LayoutError('Duplicate natural deposit identity across known zones')
    after = _number(h.field(state, 'time'))
    if after < before or after - before > 5:
        raise LayoutError('Known-zone game clock changed outside sampling bounds')
    return {'schema_version': 1, 'status': 'partial', 'source': SOURCE, 'scope': SCOPE,
            'hlboot_sha256': HLBOOT_SHA256, 'data_cdb_sha256': DATA_CDB_SHA256,
            **{key: identity[key] for key in IDENTITIES}, 'local_player_entity_uid': uid,
            'game_time_seconds': after, 'time_before': before, 'time_after': after,
            'sample_span_game_seconds': after - before, 'atomic': False,
            'zones': zones, 'reason_codes': [], 'unknown_fields': list(UNKNOWNS)}
