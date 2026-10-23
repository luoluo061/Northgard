"""Pure strict whitelist for already-discovered static map observations."""
from __future__ import annotations

import math
import re

SOURCE = 'native_discovered_static_map_read_only'
HLBOOT_SHA256 = 'db0459d251879698420f58f6819e0ab2f75a3cde6e65611f2ad723f3b4b7ef76'
DATA_CDB_SHA256 = '88d4c84b9622ce237533afc8674174aa9006d87a03258cd7e5fd671f930174e3'
IDENTITIES = ('session_key', 'capture_run_id', 'capture_segment_id')
KINDS = frozenset(('Default', 'Water', 'Coast'))
_TOKEN = re.compile(r'[A-Za-z][A-Za-z0-9_.]{0,95}\Z')
_IDENTITY = re.compile(r'[A-Za-z0-9_:-]{1,128}\Z')


def _int32(value):
    return type(value) is int and -(2**31) <= value < 2**31


def _id(value):
    return _int32(value) and value >= 0


def _ids(value):
    return (type(value) is list and len(value) <= 10000
            and all(_id(x) for x in value) and len(value) == len(set(value)))


def _number(value):
    if type(value) is int:
        return abs(value) < 2**53
    return type(value) is float and math.isfinite(value) and abs(value) < 2**53


def _reject(reason):
    # Fixed diagnostics only: never copy source errors, arbitrary keys or paths.
    return {'schema_version': 1, 'status': 'rejected', 'source': SOURCE,
            'scope': 'no_map_data', 'reason_codes': [reason],
            'unknown_fields': ['map']}


def project_map(value, snapshot):
    """Normalize a raw map component against its verified parent observation."""
    if (type(value) is not dict or type(snapshot) is not dict
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value.get('status') != 'ok' or value.get('source') != SOURCE
            or snapshot.get('status') != 'ok'):
        return _reject('invalid_map_component')
    for key in IDENTITIES:
        if (type(snapshot.get(key)) is not str or not _IDENTITY.fullmatch(snapshot[key])
                or value.get(key) != snapshot[key]):
            return _reject('map_capture_identity_mismatch')
    if (value.get('hlboot_sha256') != HLBOOT_SHA256
            or value.get('data_cdb_sha256') != DATA_CDB_SHA256):
        return _reject('map_build_unverified')
    own_uid = snapshot.get('local_player_entity_uid')
    identity = snapshot.get('local_player_identity')
    if (not _id(own_uid) or own_uid == 0 or type(identity) is not dict
            or identity.get('verified') is not True or identity.get('role') != 'player'
            or not _id(identity.get('entity_uid')) or identity['entity_uid'] != own_uid
            or identity.get('is_ai') is not False
            or identity.get('in_player_list') is not True
            or ('local_player_entity_uid' in value
                and (not _id(value['local_player_entity_uid'])
                     or value['local_player_entity_uid'] != own_uid))):
        return _reject('map_local_player_identity_unverified')
    if not _ids(snapshot.get('discovered_zone_ids')) or not _ids(snapshot.get('owned_zone_ids')):
        return _reject('map_parent_knowledge_unverified')
    context = snapshot.get('game_context')
    if type(context) is not dict or context.get('editor_present') is not False:
        return _reject('map_editor_context_unverified')
    when, parent_when = value.get('game_time_seconds'), snapshot.get('game_time_seconds')
    if (not _number(when) or when < 0 or not _number(parent_when)
            or not -0.025 <= parent_when - when <= 5):
        return _reject('map_game_clock_mismatch')
    known = set(snapshot['discovered_zone_ids']) | set(snapshot['owned_zone_ids'])
    map_identity = value.get('map_identity')
    if (type(map_identity) is not dict
            or map_identity.get('map_instance_id') != snapshot['session_key']
            or type(map_identity.get('biome')) is not str
            or not _TOKEN.fullmatch(map_identity['biome'])):
        return _reject('map_instance_identity_invalid')
    if context.get('biome') is not None and map_identity['biome'] != context['biome']:
        return _reject('map_instance_identity_invalid')
    projected_identity = {'map_instance_id': snapshot['session_key'], 'biome': map_identity['biome']}
    unknown = ['current_visibility', 'fog_hidden_dynamic_entities',
               'resource_deposit_remaining', 'non_owned_building_state']
    for key, source_key, allowed_source in (
            ('seed', 'seed_source', 'Game.config.init.seed'),
            ('generation_seed', 'generation_seed_source', 'Game.generator.seed')):
        seed, source = map_identity.get(key), map_identity.get(source_key)
        if seed is None and source is None:
            projected_identity[key] = projected_identity[source_key] = None
            unknown.append('map_identity.' + key)
        elif not _int32(seed) or source != allowed_source:
            return _reject('map_seed_invalid_or_unverified')
        else:
            projected_identity[key], projected_identity[source_key] = seed, allowed_source
    zones = value.get('known_zones')
    if type(zones) is not list or len(zones) > 10000:
        return _reject('map_known_cells_invalid')
    output, seen = [], set()
    for zone in zones:
        if (type(zone) is not dict or not _id(zone.get('id'))
                or zone['id'] not in known or zone['id'] in seen):
            return _reject('map_cell_outside_parent_knowledge')
        seen.add(zone['id'])
        position = zone.get('position')
        neighbors = zone.get('known_geometric_neighbor_ids')
        if (type(zone.get('terrain_kind')) is not str or zone['terrain_kind'] not in KINDS
                or type(position) is not dict
                or not _number(position.get('x')) or not _number(position.get('y'))
                or not _int32(zone.get('floor')) or type(zone.get('has_lake')) is not bool
                or not _ids(neighbors) or zone['id'] in neighbors
                or not set(neighbors).issubset(known)):
            return _reject('map_cell_fields_or_neighbors_invalid')
        output.append({'id': zone['id'], 'terrain_kind': zone['terrain_kind'],
                       'position': {'x': position['x'], 'y': position['y']},
                       'floor': zone['floor'], 'has_lake': zone['has_lake'],
                       'known_geometric_neighbor_ids': sorted(neighbors)})
    return {'schema_version': 1, 'status': 'ok', 'source': SOURCE,
            'policy_version': 'player-map.v1',
            'scope': 'local_player_discovered_static_cells',
            'visibility_policy': 'discovery_does_not_establish_current_visibility',
            'adjacency_semantics': 'geometric_map_cell_neighbors_not_traversability',
            'hlboot_sha256': HLBOOT_SHA256, 'data_cdb_sha256': DATA_CDB_SHA256,
            'game_time_seconds': when, 'local_player_entity_uid': own_uid,
            'map_identity': projected_identity,
            'known_zones': sorted(output, key=lambda x: x['id']),
            'unknown_fields': unknown, 'reason_codes': [],
            **{key: snapshot[key] for key in IDENTITIES}}
