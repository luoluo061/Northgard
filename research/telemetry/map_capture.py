"""Read only discovered static map cells, with no fog-hidden entity details.

Geometric cell adjacency is not a promise of current unit traversability.
All unknown neighbors are filtered by pointer before their fields are read.
No game functions, UI, process writes or dynamic getters are used.
"""
from __future__ import annotations

import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'runtime'))
from resource_caps import VERIFIED_HASH, VERIFIED_CDB_HASH
from runtime_decode import enum_values
from runtime_types import LayoutError

IDENTITY_FIELDS = ('session_key', 'capture_run_id', 'capture_segment_id')
KINDS = frozenset(('Default', 'Water', 'Coast'))


def _int(value):
    if isinstance(value, bool) or not isinstance(value, int) or not -(2**31) <= value < 2**31:
        raise LayoutError('Invalid static-map integer')
    return value


def _float(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise LayoutError('Invalid static-map coordinate')
    return float(value)


def _string(h, address):
    if not address or h.object_type(address).name != 'String':
        raise LayoutError('Expected static-map String')
    count = h.field(address, 'length')
    if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 128:
        raise LayoutError('Invalid static-map String length')
    return h.read(h.field(address, 'bytes'), count * 2).decode('utf-16-le')


def _objects(h, address, limit=10000):
    for _ in range(5):
        name = h.object_type(address).name
        if name == 'hl.types.ArrayObj':
            count = h.field(address, 'length')
            if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= limit:
                raise LayoutError('Invalid static-map array size')
            array = h.native_array(h.field(address, 'array'), limit=limit * 2)
            if count > array['size']:
                raise LayoutError('Static-map array exceeds capacity')
            return array['values'][:count]
        if name in ('hxbit.ArrayProxyData', 'hl.types.ArrayDyn'):
            address = h.field(address, 'array')
            continue
        raise LayoutError('Unexpected static-map array wrapper')
    raise LayoutError('Static-map array nesting exceeded')


def _materialized(h, address, wanted, expected_kind):
    """Read exactly one virtual slot; never inspect unrelated optional fields."""
    info = h.object_type(address)
    if info.kind != 15:
        raise LayoutError('Expected static-map virtual record')
    fields = h.ptr(info.parameter)
    count = h.unpack(info.parameter + 8, 'i')
    if not 0 <= count <= 128:
        raise LayoutError('Invalid static-map virtual field count')
    for index in range(count):
        field = fields + index * 24
        if h.utf16(h.ptr(field)) != wanted:
            continue
        tp = h.ptr(field + 8)
        if h.type_info(tp).kind != expected_kind:
            raise LayoutError('Static-map virtual field has unexpected type')
        slot = h.ptr(address + 24 + index * 8)
        if not slot:
            raise LayoutError('Static-map virtual field is not materialized')
        return h.value_at(slot, tp)
    raise LayoutError('Static-map virtual field absent')


def _seed_identity(h, game):
    """Keep configured and generator seeds separate; retries may differ."""
    result = {'seed': None, 'seed_source': None,
              'generation_seed': None, 'generation_seed_source': None}
    config = h.field(game, 'config')
    if config:
        try:
            init = _materialized(h, config, 'init', 15)
            if init:
                result['seed'] = _int(_materialized(h, init, 'seed', 3))
                result['seed_source'] = 'Game.config.init.seed'
        except (KeyError, LayoutError):
            # Optional seed data may be absent after a load; do not call getters.
            pass
    generator = h.field(game, 'generator')
    if generator:
        if h.object_type(generator).name != 'map.Generator':
            raise LayoutError('Unexpected map generator type')
        if h.field(generator, 'game') != game:
            raise LayoutError('Generator is not bound to this game')
        result['generation_seed'] = _int(h.field(generator, 'seed'))
        result['generation_seed_source'] = 'Game.generator.seed'
    return result


def capture_map(h, me, state, *, known_zone_ids, identity, profile):
    """Return a whitelist of cells already in the local discovered ID set.

    Runtime hashes must already have been checked by Probe. Caller-provided IDs
    are independently checked against me.world.discoveredIds. This never reads
    buildings, mineral stocks, enemies, owners, decorators or fog-hidden entity
    arrays. A discovered tile does not establish current visibility.
    """
    if (profile.get('hlboot_sha256') != VERIFIED_HASH
            or profile.get('data_cdb_sha256') != VERIFIED_CDB_HASH):
        raise LayoutError('Static map semantics are not verified for this build/data')
    if not identity or any(not isinstance(identity.get(key), str) or not identity[key]
                           for key in IDENTITY_FIELDS):
        raise LayoutError('Static map capture requires complete capture identity')
    if (h.object_type(me).name != 'ent.Player' or h.object_type(state).name != 'GameState'
            or h.field(state, 'me') != me):
        raise LayoutError('Static map scope is not the local player')
    if h.field(state, 'editor'):
        raise LayoutError('Editable map geometry is not a supported static-map context')
    requested = [_int(value) for value in known_zone_ids]
    if len(requested) != len(set(requested)):
        raise LayoutError('Duplicate requested discovered zone ID')
    wanted = set(requested)
    world = h.field(me, 'world')
    if not world or h.object_type(world).name != 'ent.player.WorldComponent':
        raise LayoutError('Expected local WorldComponent')
    discovered = set(h.int_array(h.field(world, 'discoveredIds')))
    if not wanted.issubset(discovered):
        raise LayoutError('Requested map data includes an undiscovered zone')
    game = h.field(state, 'game')
    if not game or h.object_type(game).name != 'Game' or h.field(game, 'state') != state:
        raise LayoutError('Static map game/state scope mismatch')
    map_identity = {'map_instance_id': identity['session_key'],
                    'biome': _string(h, h.field(state, 'biome')),
                    **_seed_identity(h, game)}
    cells = {}
    seen_zone_ids = set()
    for zone in _objects(h, h.field(state, 'zones')):
        # Resolving requested IDs reads IDs only for the other map objects.
        # Their terrain, ownership, contents and adjacency are not accessed.
        if h.object_type(zone).name != 'ent.Zone':
            raise LayoutError('Unexpected zone type in current map')
        zid = _int(h.field(zone, 'id'))
        if zid not in wanted:
            continue
        if zid in seen_zone_ids:
            raise LayoutError('Duplicate requested zone in map')
        seen_zone_ids.add(zid)
        cell = h.field(zone, 'cell')
        if (not cell or h.object_type(cell).name != 'map.Cell'
                or h.field(cell, 'id') != zid or cell in cells):
            raise LayoutError('Known zone does not match its static map cell')
        cells[cell] = zid
    if seen_zone_ids != wanted:
        raise LayoutError('Requested discovered zone is unavailable')
    output = []
    for cell, zid in cells.items():
        enum = h.field(cell, 'kind')
        if h.object_type(enum).name != 'enum:map.CellKind':
            raise LayoutError('Unexpected static terrain enum type')
        kind, parameters = enum_values(h, enum)
        if kind not in KINDS or parameters:
            raise LayoutError('Unsupported static terrain enum constructor')
        has_lake = h.field(cell, 'hasLake')
        if not isinstance(has_lake, bool):
            raise LayoutError('Invalid static lake flag')
        # Filter by known pointers; no fields of unknown neighbors are read.
        adjacent = {cells[p] for p in _objects(h, h.field(cell, 'neighbors')) if p in cells}
        if zid in adjacent:
            raise LayoutError('Static cell lists itself as a neighbor')
        output.append({'id': zid, 'terrain_kind': kind,
                       'position': {'x': _float(h.field(cell, 'x')),
                                    'y': _float(h.field(cell, 'y'))},
                       'floor': _int(h.field(cell, 'floor')), 'has_lake': has_lake,
                       'known_geometric_neighbor_ids': sorted(adjacent)})
    if (h.field(state, 'me') != me or h.field(me, 'world') != world
            or h.field(state, 'game') != game or h.field(game, 'state') != state
            or not wanted.issubset(set(h.int_array(h.field(world, 'discoveredIds'))))):
        raise LayoutError('Map/knowledge scope changed during capture')
    return {'schema_version': 1, 'status': 'ok',
            'source': 'native_discovered_static_map_read_only',
            'hlboot_sha256': VERIFIED_HASH, 'data_cdb_sha256': VERIFIED_CDB_HASH,
            'local_player_entity_uid': _int(h.field(me, '__uid')),
            'game_time_seconds': _float(h.field(state, 'time')),
            'knowledge_scope': 'local_player_discovered_static_cells',
            'visibility_policy': 'discovery_does_not_establish_current_visibility',
            'adjacency_semantics': 'geometric_map_cell_neighbors_not_traversability',
            'map_identity': map_identity,
            'known_zones': sorted(output, key=lambda x: x['id']),
            'unknown_fields': ['current_visibility', 'fog_hidden_dynamic_entities',
                               'resource_deposit_remaining', 'non_owned_building_state'],
            **{key: identity[key] for key in IDENTITY_FIELDS}}
