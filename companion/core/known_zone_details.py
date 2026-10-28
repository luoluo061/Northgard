"""Strict projection of supported, already-discovered natural map details.

This is a field/semantics contract, not a visibility detector. The native
adapter must establish current object membership and its audited visibility
branch. Unknown and unsupported readings never become empty-map claims.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

SOURCE = 'native_discovered_natural_details_read_only'
SCOPE = 'local_discovered_natural_resources_and_unowned_wolf_bear'
HLBOOT_SHA256 = 'db0459d251879698420f58f6819e0ab2f75a3cde6e65611f2ad723f3b4b7ef76'
DATA_CDB_SHA256 = '88d4c84b9622ce237533afc8674174aa9006d87a03258cd7e5fd671f930174e3'
IDENTITIES = ('session_key', 'capture_run_id', 'capture_segment_id')
UNKNOWNS = ['unsupported_resource_kinds', 'unsupported_creature_kinds',
            'effective_resource_yield', 'hostility_and_colonization_legality',
            'respawn_schedule']
KINDS = ('RuneStone', 'CircleStone', 'Fertility', 'Stones', 'IronDeposit',
         'Shoal', 'Grove', 'DeerSpawner')
MINERALS = frozenset(('Stones', 'IronDeposit'))
CREATURES = frozenset(('Wolf', 'Bear'))
REASONS = frozenset((
    'known_zone_context_unsupported', 'known_zone_component_read_failed',
    'known_zone_resources_unavailable', 'known_zone_wildlife_unavailable',
    'known_zone_attributes_unavailable', 'known_zone_hidden_resources_omitted',
    'known_zone_hidden_wildlife_omitted', 'known_zone_visibility_unsupported',
    'known_zone_grass_visibility_unsupported', 'known_zone_mineral_state_unavailable',
    'known_zone_resource_read_race', 'known_zone_wildlife_read_race',
    'known_zone_garrisoned_wildlife_unsupported', 'known_zone_optional_read_failed',
    'known_zone_roots_changed', 'known_zone_resource_read_failed',
    'known_zone_wildlife_read_failed', 'known_zone_natural_attributes_unavailable',
    'known_zone_resource_state_missing', 'known_zone_resource_visibility_unsupported',
    'known_zone_resource_type_unsupported', 'known_zone_wildlife_visibility_unsupported',
    'known_zone_wildlife_type_unsupported', 'known_zone_shoal_enablement_unsupported',
))
_IDENTITY = re.compile(r'[A-Za-z0-9_:-]{1,128}\Z')
_TOP = {'schema_version', 'status', 'source', 'scope', 'hlboot_sha256',
        'data_cdb_sha256', 'local_player_entity_uid', *IDENTITIES,
        'game_time_seconds', 'time_before', 'time_after',
        'sample_span_game_seconds', 'atomic', 'zones', 'reason_codes', 'unknown_fields'}
_ZONE = {'zone_id', 'resources_status', 'wildlife_status',
         'natural_attributes_status', 'tall_grass_present', 'hiding_grass_present',
         'resources', 'wildlife', 'resource_reason_codes', 'wildlife_reason_codes'}
_RESOURCE = {'kind', 'object_count', 'deposits', 'remaining_total_raw',
             'modeled_ui_remaining_total'}
_DEPOSIT = {'natural_object_uid', 'remaining_contains_raw', 'next_extraction_progress_raw'}
_DIAGNOSTIC = {'schema_version', 'status', 'source', 'scope',
               'reason_codes', 'unknown_fields', 'zones'}
DIAGNOSTIC_REASONS = frozenset(('known_zone_context_unsupported',
    'known_zone_component_unavailable', 'invalid_known_zone_component'))


def _reference(channels, slots=0, multipliers=(), *, depletion='production_opportunity',
               deer_max=None, deer_speed=None):
    return {'semantics': 'verified_default_configuration_not_effective_yield',
            'production_channels': list(channels), 'fixed_assignment_slots': slots,
            'local_multiplier_reference': [
                {'resource': resource, 'multiplier': multiplier, 'scope': 'ordinary_viking'}
                for resource, multiplier in multipliers],
            'deer_spawn_max': deer_max, 'deer_spawn_speed_config': deer_speed,
            'depletion_model': depletion}


# Reviewed numeric reference inputs, not full game definitions. These are bound
# to both pinned hashes and never promoted to native effective production.
RESOURCE_REFERENCES = {
    'RuneStone': _reference(('Lore', 'Faith'), 1),
    'CircleStone': _reference(('Lore', 'Faith'), 2, (('Lore', 1.1),)),
    'Fertility': _reference(('Wheat',)),
    'Stones': _reference(('Stone',), None, depletion='finite_mineral'),
    'IronDeposit': _reference(('Iron',), None, depletion='finite_mineral'),
    'Shoal': _reference(('Fish',)),
    'Grove': _reference(('Wood',), multipliers=(('Wood', 1.1),)),
    'DeerSpawner': _reference(('Meat',), depletion='renewing_deer_source', deer_max=7, deer_speed=10),
}


def _int(value, minimum=0, maximum=2**31-1):
    return type(value) is int and minimum <= value <= maximum


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value < 2**53


def _codes(value, allowed):
    return (type(value) is list and len(value) <= len(allowed)
            and all(type(code) is str and code in allowed for code in value)
            and len(value) == len(set(value)))


def _require(test):
    if not test:
        raise ValueError('Invalid known-zone details contract')


def _same_typed(left, right):
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(_same_typed(left[key], right[key]) for key in right)
    if type(left) is list:
        return len(left) == len(right) and all(_same_typed(a, b) for a, b in zip(left, right))
    return left == right


def _supported_context(parent):
    context = parent.get('game_context')
    if type(context) is not dict:
        return False
    if context.get('editor_present') is not False or context.get('bifrost_data_present') is not False:
        return False
    if 'mode' in context:
        return (context['mode'] == {'kind': 'Singleplayer', 'args': [{'kind': 'FreeForAll', 'args': []}]}
                and context.get('game_type') == {'kind': 'Standard', 'args': []})
    return (context.get('mode_kind') == 'Singleplayer' and context.get('mode_variant_kind') == 'FreeForAll'
            and context.get('game_type_kind') == 'Standard')


def validate_known_zone_details(value, parent, *, projected=True):
    """Raise ValueError on malformed/unscoped data; never touches a process.

    The contribution verifier also carries an independent frozen schema and
    semantics check. This routine supports local diagnostics/report consumers.
    """
    _require(type(value) is dict and type(parent) is dict)
    if value.get('status') in ('unsupported_context', 'unavailable', 'rejected'):
        _require(set(value) == _DIAGNOSTIC and type(value['schema_version']) is int
                 and value['schema_version'] == 1 and value['source'] == SOURCE
                 and value['scope'] == 'no_known_zone_details' and value['zones'] == []
                 and value['unknown_fields'] == ['known_zone_details']
                 and _codes(value['reason_codes'], DIAGNOSTIC_REASONS)
                 and len(value['reason_codes']) == 1)
        expected = {'unsupported_context': 'known_zone_context_unsupported',
                    'unavailable': 'known_zone_component_unavailable',
                    'rejected': 'invalid_known_zone_component'}
        _require(value['reason_codes'] == [expected[value['status']]])
        return
    _require(set(value) == _TOP and type(value['schema_version']) is int
             and value['schema_version'] == 1 and value['status'] == 'partial'
             and value['source'] == SOURCE and value['scope'] == SCOPE
             and value['hlboot_sha256'] == HLBOOT_SHA256
             and value['data_cdb_sha256'] == DATA_CDB_SHA256
             and value['atomic'] is False and parent.get('status') == 'ok'
             and _supported_context(parent))
    for key in IDENTITIES:
        _require(type(parent.get(key)) is str and _IDENTITY.fullmatch(parent[key])
                 and value[key] == parent[key])
    _require(_int(value['local_player_entity_uid'], 1, 2**53-1)
             and value['local_player_entity_uid'] == parent.get('local_player_entity_uid'))
    if not projected:
        identity = parent.get('local_player_identity')
        _require(type(identity) is dict and identity.get('verified') is True
                 and identity.get('role') == 'player' and identity.get('is_ai') is False
                 and identity.get('in_player_list') is True
                 and identity.get('entity_uid') == value['local_player_entity_uid'])
    for key in ('game_time_seconds', 'time_before', 'time_after', 'sample_span_game_seconds'):
        _require(_number(value[key]))
    _require(_number(parent.get('game_time_seconds'))
             and value['time_after'] >= value['time_before']
             and value['game_time_seconds'] == value['time_after']
             and math.isclose(value['time_after'] - value['time_before'], value['sample_span_game_seconds'], abs_tol=1e-9)
             and value['sample_span_game_seconds'] <= 5
             and -0.025 <= parent['game_time_seconds'] - value['time_after'] <= 5)
    _require(value['unknown_fields'] == UNKNOWNS and _codes(value['reason_codes'], REASONS))
    known = parent.get('discovered_zone_ids')
    _require(type(known) is list and len(known) <= 10000
             and all(_int(zid) for zid in known) and len(known) == len(set(known)))
    zones = value['zones']
    _require(type(zones) is list and len(zones) == len(known))
    seen_zones, seen_deposits = set(), set()
    own_buildings = {b.get('uid'): b for b in parent.get('buildings', []) if type(b) is dict}
    own_units = {u.get('uid') for u in parent.get('units', []) if type(u) is dict}
    for zone in zones:
        _require(type(zone) is dict and set(zone) == _ZONE
                 and _int(zone['zone_id']) and zone['zone_id'] in known
                 and zone['zone_id'] not in seen_zones)
        seen_zones.add(zone['zone_id'])
        _require(zone['resources_status'] in ('ok', 'partial', 'unavailable')
                 and zone['wildlife_status'] in ('ok', 'partial', 'unavailable')
                 and zone['natural_attributes_status'] in ('complete', 'unavailable')
                 and _codes(zone['resource_reason_codes'], REASONS)
                 and _codes(zone['wildlife_reason_codes'], REASONS))
        if zone['natural_attributes_status'] == 'complete':
            _require(type(zone['tall_grass_present']) is bool and type(zone['hiding_grass_present']) is bool)
        else:
            _require(zone['tall_grass_present'] is None and zone['hiding_grass_present'] is None)
        if zone['wildlife_status'] == 'ok':
            _require(zone['natural_attributes_status'] == 'complete'
                     and zone['tall_grass_present'] is False and zone['hiding_grass_present'] is False)
        _require(type(zone['resources']) is list and len(zone['resources']) <= len(KINDS)
                 and type(zone['wildlife']) is list and len(zone['wildlife']) <= len(CREATURES))
        if zone['resources_status'] == 'unavailable':
            _require(zone['resources'] == [])
        if zone['wildlife_status'] == 'unavailable':
            _require(zone['wildlife'] == [])
        resource_kinds = set()
        for resource in zone['resources']:
            keys = _RESOURCE | ({'reference_properties'} if projected else set())
            _require(type(resource) is dict and set(resource) == keys
                     and type(resource['kind']) is str and resource['kind'] in KINDS and resource['kind'] not in resource_kinds
                     and _int(resource['object_count'], 1, 10000))
            kind = resource['kind']; resource_kinds.add(kind)
            if projected:
                _require(_same_typed(resource['reference_properties'], RESOURCE_REFERENCES[kind]))
            deposits = resource['deposits']
            _require(type(deposits) is list and len(deposits) <= 10000)
            if kind not in MINERALS:
                _require(deposits == [] and resource['remaining_total_raw'] is None
                         and resource['modeled_ui_remaining_total'] is None)
                continue
            _require(len(deposits) == resource['object_count'])
            amounts = []
            for deposit in deposits:
                _require(type(deposit) is dict and set(deposit) == _DEPOSIT)
                uid = deposit['natural_object_uid']
                _require(_int(uid, 1, 2**53-1) and uid not in seen_deposits and uid not in own_units)
                seen_deposits.add(uid)
                if uid in own_buildings:
                    _require(own_buildings[uid].get('kind') == kind
                             and own_buildings[uid].get('zone_id') == zone['zone_id'])
                for name in ('remaining_contains_raw', 'next_extraction_progress_raw'):
                    _require(deposit[name] is None or _number(deposit[name]))
                amounts.append(deposit['remaining_contains_raw'])
            complete = zone['resources_status'] == 'ok' and all(amount is not None for amount in amounts)
            if complete:
                total = sum(amounts)
                _require(_number(resource['remaining_total_raw'])
                         and resource['remaining_total_raw'] == total
                         and _int(resource['modeled_ui_remaining_total'], 0, 2**53-1)
                         and resource['modeled_ui_remaining_total'] == math.ceil(total))
            else:
                _require(resource['remaining_total_raw'] is None and resource['modeled_ui_remaining_total'] is None)
        creatures = set()
        for creature in zone['wildlife']:
            _require(type(creature) is dict and set(creature) == {'kind', 'count'}
                     and type(creature['kind']) is str and creature['kind'] in CREATURES and creature['kind'] not in creatures
                     and _int(creature['count'], 0 if zone['wildlife_status'] == 'ok' else 1, 10000))
            creatures.add(creature['kind'])


def _diagnostic(status):
    reasons = {'unsupported_context': 'known_zone_context_unsupported',
               'unavailable': 'known_zone_component_unavailable',
               'rejected': 'invalid_known_zone_component'}
    return {'schema_version': 1, 'status': status, 'source': SOURCE,
            'scope': 'no_known_zone_details', 'reason_codes': [reasons[status]],
            'unknown_fields': ['known_zone_details'], 'zones': []}


def project_known_zone_details(value, snapshot):
    """Project only the versioned optional component; failure preserves core."""
    if type(value) is dict and value.get('source') == SOURCE and type(value.get('schema_version')) is int and value['schema_version'] == 1:
        if value.get('status') in ('unsupported_context', 'unavailable'):
            return _diagnostic(value['status'])
    try:
        validate_known_zone_details(value, snapshot, projected=False)
        result = deepcopy(value)
        for zone in result['zones']:
            for resource in zone['resources']:
                resource['reference_properties'] = deepcopy(RESOURCE_REFERENCES[resource['kind']])
        validate_known_zone_details(result, snapshot)
        return result
    except (ValueError, TypeError, KeyError, OverflowError):
        return _diagnostic('rejected')
