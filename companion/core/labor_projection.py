"""Strict, pointer-free allowlist for own labor-input observations.

Configuration candidates never become native per-unit output. The projection
always recreates unavailable rate fields as null, even if its input claims a
numeric result. Parent identity, game clock and own-object scope are required.
"""
from __future__ import annotations

import math
import re

SOURCE = 'native_own_labor_inputs_read_only'
HLBOOT_SHA256 = 'db0459d251879698420f58f6819e0ab2f75a3cde6e65611f2ad723f3b4b7ef76'
DATA_CDB_SHA256 = '88d4c84b9622ce237533afc8674174aa9006d87a03258cd7e5fd671f930174e3'
IDENTITIES = ('session_key', 'capture_run_id', 'capture_segment_id')
_TOKEN = re.compile(r'[A-Za-z][A-Za-z0-9_.]{0,95}\Z')
_IDENTITY = re.compile(r'[A-Za-z0-9_:-]{1,128}\Z')
RATE_STATUS = 'computed_getter_no_persistent_unit_cache'
KNOWN_REASONS = frozenset(('labor_local_rules_unavailable', 'labor_optional_input_unavailable',
    'labor_modifier_map_unavailable', 'labor_modifier_component_unavailable',
    'labor_effect_map_unavailable', 'labor_context_scope_unverified',
    'labor_non_owned_or_changed_context_omitted', 'labor_non_owned_or_changed_unit_omitted'))
KNOWN_UNKNOWNS = frozenset(('configuration_candidates', 'optional_native_input', 'researched_tech_entries',
    'upgraded_unit_entries', 'special_tech_provider_present', 'resources', 'technology', 'upgrades',
    'player_effects', 'zones', 'zones.optional_inputs', 'buildings', 'building_effects',
    'building_state', 'units', 'unit_effects'))
UNIT_BOOL_FIELDS = ('has_no_work', 'just_assigned', 'enabled')
UNIT_NUMBER_FIELDS = ('hit_life', 'sick_counter', 'status_timer')
UNIT_TOKEN_FIELDS = ('produce', 'sick_reason', 'status', 'cur_job_kind', 'assigned_job')
PLAYER_BOOL_FIELDS = ('in_winter', 'overworking', 'special_tech_provider_present', 'owl_component_present')
PLAYER_NUMBER_FIELDS = ('happiness_cache', 'consumption_coefficient', 'last_feast_time', 'last_overwork_time')
PLAYER_INTEGER_FIELDS = ('population_cache', 'fame_level')


def _integer(value):
    return type(value) is int and 0 <= value < 2**31


def _flags(value):
    return type(value) is int and -(2**31) <= value < 2**31


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) < 2**53


def _token(value):
    return type(value) is str and _TOKEN.fullmatch(value) is not None


def _reject(reason):
    return {'schema_version': 1, 'source': SOURCE, 'status': 'rejected',
        'scope': 'no_labor_data', 'reason_codes': [reason], 'unknown_fields': ['all'],
        'atomic': False, 'native_individual_rates_available': False,
        'actual_settled_amounts_available': False, 'individual_rate_status': RATE_STATUS}


def _effects(value):
    if type(value) is not list or len(value) > 1024:
        return None
    output, seen = [], set()
    for row in value:
        if (type(row) is not dict or not _token(row.get('kind')) or row['kind'] in seen
                or not _number(row.get('raw_end_time'))):
            return None
        seen.add(row['kind'])
        output.append({'kind': row['kind'], 'raw_end_time': row['raw_end_time']})
    return output


def _entries(value):
    if type(value) is not list or len(value) > 1024:
        return None
    output, seen = [], set()
    for row in value:
        if type(row) is not dict or not _token(row.get('id')) or row['id'] in seen:
            return None
        item = row.get('value')
        if item is not None and type(item) is not bool and not _number(item):
            return None
        seen.add(row['id'])
        output.append({'id': row['id'], 'value': item})
    return output


def _candidates(value):
    if type(value) is not list or len(value) > 64:
        return None
    output = []
    for row in value:
        if (type(row) is not dict or not _token(row.get('resource'))
                or row.get('configured_base_q') is not None and not _number(row['configured_base_q'])
                or row.get('converts_to') is not None and not _token(row['converts_to'])
                or type(row.get('building_kinds')) is not list or len(row['building_kinds']) > 64
                or not all(_token(k) for k in row['building_kinds'])
                or type(row.get('additional_requirements_present')) is not bool
                or row.get('requirement_status') != 'not_evaluated'
                or row.get('rate_semantics') != 'static_configuration_candidate_not_effective_rate'):
            return None
        output.append({'resource': row['resource'], 'configured_base_q': row.get('configured_base_q'),
            'converts_to': row.get('converts_to'), 'building_kinds': list(row['building_kinds']),
            'additional_requirements_present': row['additional_requirements_present'],
            'requirement_status': 'not_evaluated',
            'rate_semantics': 'static_configuration_candidate_not_effective_rate'})
    return output


def project_labor(value, snapshot):
    """Normalize against the raw verified parent ActionProbe observation.

    No arbitrary object, enum argument, source reason/error text, property name,
    unit name, account identifier, process address or other-player record is
    copied. This is a whitelist, not an independent memory-visibility detector.
    """
    if (type(value) is not dict or type(snapshot) is not dict
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value.get('source') != SOURCE or value.get('status') not in ('ok', 'partial')
            or snapshot.get('status') != 'ok'):
        return _reject('invalid_labor_component')
    for key in IDENTITIES:
        if (type(snapshot.get(key)) is not str or not _IDENTITY.fullmatch(snapshot[key])
                or value.get(key) != snapshot[key]):
            return _reject('labor_capture_identity_mismatch')
    if value.get('hlboot_sha256') != HLBOOT_SHA256 or value.get('data_cdb_sha256') != DATA_CDB_SHA256:
        return _reject('labor_build_unverified')
    uid, identity = snapshot.get('local_player_entity_uid'), snapshot.get('local_player_identity')
    if (not _integer(uid) or uid == 0 or type(identity) is not dict or identity.get('verified') is not True
            or identity.get('role') != 'player' or identity.get('entity_uid') != uid
            or type(identity.get('entity_uid')) is not int or identity.get('is_ai') is not False
            or identity.get('in_player_list') is not True or value.get('local_player_entity_uid') != uid
            or type(value.get('local_player_entity_uid')) is not int):
        return _reject('labor_local_player_identity_unverified')
    when, parent_when = value.get('game_time_seconds'), snapshot.get('game_time_seconds')
    before, after, span = value.get('time_before'), value.get('time_after'), value.get('sample_span_game_seconds')
    if (not all(_number(v) for v in (when, parent_when, before, after, span))
            or when < 0 or abs(before-when) > .000001 or not 0 <= after-before <= 5
            or abs(after-before-span) > .000001 or not -.025 <= parent_when-after <= 5):
        return _reject('labor_game_clock_mismatch')
    owned, known = snapshot.get('owned_zone_ids'), snapshot.get('discovered_zone_ids')
    if (type(owned) is not list or type(known) is not list or len(owned) > 4096 or len(known) > 4096
            or not all(_integer(z) for z in owned+known) or len(set(owned)) != len(owned)
            or len(set(known)) != len(known)):
        return _reject('labor_parent_scope_unverified')
    owned, known = set(owned), set(known) | set(owned)
    parents = {}
    for field in ('units', 'buildings'):
        records = snapshot.get(field)
        if type(records) is not list or len(records) > 10000:
            return _reject('labor_parent_scope_unverified')
        canonical = {}
        for row in records:
            if (type(row) is not dict or not _integer(row.get('uid')) or not _token(row.get('kind'))
                    or row['uid'] in canonical or row.get('zone_id') is not None and not _integer(row['zone_id'])):
                return _reject('labor_parent_scope_unverified')
            if field == 'buildings' and row.get('zone_id') not in owned:
                return _reject('labor_parent_scope_unverified')
            if any(k in row and row[k] != uid for k in ('owner_uid','owner_entity_uid','player_uid','player_entity_uid')):
                return _reject('labor_parent_scope_unverified')
            canonical[row['uid']] = row
        parents[field] = canonical
    result = {'schema_version': 1, 'source': SOURCE, 'status': value['status'],
        'hlboot_sha256': HLBOOT_SHA256, 'data_cdb_sha256': DATA_CDB_SHA256,
        'local_player_entity_uid': uid, 'game_time_seconds': when, 'time_before': before,
        'time_after': after, 'sample_span_game_seconds': span, 'atomic': False,
        'native_individual_rates_available': False, 'actual_settled_amounts_available': False,
        'individual_rate_status': RATE_STATUS, 'player_context': {}, 'units': [], 'zones': [], 'buildings': [],
        'reason_codes': [], 'unknown_fields': [], **{k: snapshot[k] for k in IDENTITIES}}
    def unknown(field, reason):
        result['status'] = 'partial'
        if field not in result['unknown_fields']:
            result['unknown_fields'].append(field)
        if reason not in result['reason_codes']:
            result['reason_codes'].append(reason)
    for key, allowed in (('reason_codes', KNOWN_REASONS), ('unknown_fields', KNOWN_UNKNOWNS)):
        rows = value.get(key)
        if type(rows) is not list or len(rows) > 128:
            unknown('source_diagnostics', 'labor_source_diagnostics_omitted')
            continue
        for item in rows:
            if type(item) is str and item in allowed:
                if item not in result[key]:
                    result[key].append(item)
            else:
                unknown('source_diagnostics', 'labor_source_diagnostics_omitted')
    context = value.get('player_context')
    if type(context) is not dict:
        context = {}
    out = result['player_context']
    out['clan'] = context.get('clan') if _token(context.get('clan')) and context['clan'] == snapshot.get('clan') else None
    for fields, valid in ((PLAYER_BOOL_FIELDS, lambda v: type(v) is bool),
                          (PLAYER_NUMBER_FIELDS, _number), (PLAYER_INTEGER_FIELDS, _integer)):
        for key in fields:
            out[key] = context.get(key) if valid(context.get(key)) else None
    for key in ('researched_tech_entries', 'upgraded_unit_entries'):
        out[key] = _entries(context.get(key))
    out['effects'] = _effects(context.get('effects'))
    out['modifier_coverage'] = 'materialized_inputs_only_not_complete_effective_bonus'
    source_buildings = value.get('buildings')
    if type(source_buildings) is not list or len(source_buildings) > 10000:
        source_buildings = []
        unknown('buildings', 'labor_context_rows_omitted')
    accepted_buildings = {}
    for row in source_buildings:
        if (type(row) is not dict or not _integer(row.get('uid')) or row['uid'] not in parents['buildings']
                or row['uid'] in accepted_buildings or row.get('zone_id') != parents['buildings'][row['uid']].get('zone_id')
                or row.get('kind') != parents['buildings'][row['uid']]['kind']):
            unknown('buildings', 'labor_non_owned_context_omitted')
            continue
        out = {k: row[k] for k in ('uid', 'kind', 'zone_id')}
        for key in ('upgraded',):
            out[key] = row.get(key) if _integer(row.get(key)) else None
        for key in ('hit_life', 'state_progress', 'state_contains'):
            out[key] = row.get(key) if _number(row.get(key)) else None
        for key in ('extended', 'wait_repair', 'harbor_force_stop'):
            out[key] = row.get(key) if type(row.get(key)) is bool else None
        out['harbor_produce_choice'] = row.get('harbor_produce_choice') if _token(row.get('harbor_produce_choice')) else None
        out['effects'] = _effects(row.get('effects'))
        accepted_buildings[row['uid']] = out
        result['buildings'].append(out)
    source_zones = value.get('zones')
    if type(source_zones) is not list or len(source_zones) > 4096:
        source_zones = []
        unknown('zones', 'labor_context_rows_omitted')
    seen = set()
    for row in source_zones:
        if type(row) is not dict or not _integer(row.get('zone_id')) or row['zone_id'] not in owned or row['zone_id'] in seen:
            unknown('zones', 'labor_non_owned_context_omitted')
            continue
        seen.add(row['zone_id'])
        result['zones'].append({'zone_id': row['zone_id'], 'effects': _effects(row.get('effects')),
            'flags': row.get('flags') if _flags(row.get('flags')) else None})
    source_units = value.get('units')
    if type(source_units) is not list or len(source_units) > 10000:
        source_units = []
        unknown('units', 'labor_unit_rows_omitted')
    seen = set()
    for row in source_units:
        if (type(row) is not dict or not _integer(row.get('uid')) or row['uid'] not in parents['units']
                or row['uid'] in seen or row.get('kind') != parents['units'][row['uid']]['kind']
                or row.get('zone_id') != parents['units'][row['uid']].get('zone_id')):
            unknown('units', 'labor_non_owned_unit_omitted')
            continue
        seen.add(row['uid'])
        out = {'uid': row['uid'], 'kind': row['kind'],
            'zone_id': row.get('zone_id') if row.get('zone_id') in known else None,
            'native_actual_rate': None, 'native_settled_amount': None, 'model_effective_rate': None,
            'rate_status': RATE_STATUS, 'can_produce': None, 'can_produce_status': 'getter_not_executed',
            'inputs': {}, 'effects': _effects(row.get('effects')), 'assignment': None,
            'configuration_candidates': _candidates(row.get('configuration_candidates'))}
        inputs = row.get('inputs')
        if type(inputs) is not dict:
            inputs = {}
        for fields, valid in ((UNIT_BOOL_FIELDS, lambda v: type(v) is bool), (UNIT_NUMBER_FIELDS, _number),
                              (UNIT_TOKEN_FIELDS, _token), (('flags',), _flags)):
            for key in fields:
                out['inputs'][key] = inputs.get(key) if valid(inputs.get(key)) else None
        assignment = row.get('assignment')
        if (type(assignment) is dict and _integer(assignment.get('building_uid'))
                and assignment['building_uid'] in accepted_buildings
                and assignment.get('zone_id') == accepted_buildings[assignment['building_uid']]['zone_id']):
            out['assignment'] = {'building_uid': assignment['building_uid'], 'zone_id': assignment['zone_id'],
                'same_zone': row.get('zone_id') == assignment['zone_id']}
        out['assignment_status'] = ('verified_own_building' if out['assignment'] is not None else
            'no_assignment' if row.get('assignment_status') == 'no_assignment' else
            'non_owned_or_unverified_reference_omitted')
        result['units'].append(out)
    return result
