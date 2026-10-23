"""Read materialized inputs to own units' production; never execute a getter.

The verified game computes Unit.getProduction on demand. It does not retain a
per-unit final-rate or settled-amount cache. This adapter deliberately returns
null for those quantities. Static q values are separate configuration
candidates, not measured production and not a prediction of effective output.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import struct
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'runtime'))
from runtime_types import LayoutError
from live_reader import objects, string
from runtime_decode import enum_values

SOURCE = 'native_own_labor_inputs_read_only'
VERIFIED_HASH = 'db0459d251879698420f58f6819e0ab2f75a3cde6e65611f2ad723f3b4b7ef76'
VERIFIED_CDB_HASH = '88d4c84b9622ce237533afc8674174aa9006d87a03258cd7e5fd671f930174e3'
IDENTITIES = ('session_key', 'capture_run_id', 'capture_segment_id')
_TOKEN = re.compile(r'[A-Za-z][A-Za-z0-9_.]{0,95}\Z')
ERRORS = (OSError, ValueError, KeyError, TypeError, struct.error, OverflowError)


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) >= 2**53:
        raise LayoutError('Invalid labor numeric input')
    return value


def _integer(value):
    if type(value) is not int or not 0 <= value < 2**31:
        raise LayoutError('Invalid labor identifier')
    return value


def _bool(value):
    if type(value) is not bool:
        raise LayoutError('Invalid labor boolean')
    return value


def _token(value):
    if type(value) is not str or not _TOKEN.fullmatch(value):
        raise LayoutError('Invalid labor identifier string')
    return value


def _nullable_string(h, pointer):
    return _token(string(h, pointer)) if pointer else None


def _flags(h, pointer):
    if not pointer or h.object_type(pointer).name != 'hxbit.EnumFlagsData':
        raise LayoutError('Expected materialized enum flags')
    value = h.field(pointer, 'value')
    if type(value) is not int or not -(2**31) <= value < 2**31:
        raise LayoutError('Invalid materialized flags value')
    return value


def _map(h, pointer):
    """Only scalar gameplay entries; no recursive dynamic/object serialization."""
    if not pointer:
        raise LayoutError('Missing labor state map')
    before = h.string_map(pointer, limit=1024)
    output = []
    for key, value in sorted(before.items()):
        _token(key)
        if type(value) is bool:
            pass
        elif value is None:
            pass
        else:
            _number(value)
        output.append({'id': key, 'value': value})
    if before != h.string_map(pointer, limit=1024):
        raise LayoutError('Labor map changed during read')
    return output


def _effects(h, entity):
    evars = h.field(entity, 'evars')
    if not evars or h.object_type(evars).name != 'ent.EntityVariables':
        raise LayoutError('Expected own entity effect variables')
    entries = _map(h, h.field(evars, 'affectsTime'))
    # Expiry may be a sentinel or may already have passed. Do not call these
    # active buffs or turn their timestamps into a multiplier.
    for entry in entries:
        _number(entry['value'])
    return [{'kind': item['id'], 'raw_end_time': item['value']} for item in entries]


def _job_kind(h, pointer):
    if not pointer:
        return None
    kind, _ = enum_values(h, pointer)
    # Enum arguments can reference other players/units and are never traversed.
    return _token(kind)


def _read_scalar(h, pointer, name, validator):
    return validator(h.field(pointer, name))


def _boxed_nullable_float(h, address):
    """null<f64> fields contain boxed dynamics, not an inline double.

    A non-null integer here is a process address and must never escape as a
    numeric progress/count. The metadata for Building.state confirms null<f64>.
    """
    if not address:
        return None
    if type(address) is not int or h.object_type(address).kind != 6:
        raise LayoutError('Expected boxed nullable Float labor input')
    return _number(h.dynamic(address))


def _rules(local_rules, profile):
    if local_rules is None:
        try:
            local_rules = json.loads((HERE.parent / 'static/labor_rules.json').read_text(encoding='utf8'))
        except (OSError, ValueError):
            return None
    if (type(local_rules) is not dict or type(local_rules.get('schema_version')) is not int
            or local_rules['schema_version'] != 1
            or local_rules.get('hlboot_sha256') != profile.get('hlboot_sha256')
            or local_rules.get('data_cdb_sha256') != profile.get('data_cdb_sha256')
            or type(local_rules.get('tables')) is not dict):
        return None
    table = local_rules['tables'].get('unit')
    resources = local_rules['tables'].get('resource')
    if type(table) is not list or type(resources) is not list or len(table) > 4096 or len(resources) > 1024:
        return None
    # Fail closed on malformed/ambiguous identifiers. Preserve only the small
    # fields used below; never copy arbitrary local data into a capture.
    try:
        indexed = {_token(r['id']): r for r in table}
        converted = {_token(r['id']): r.get('props', {}).get('converts') for r in resources}
        if len(indexed) != len(table) or len(converted) != len(resources):
            return None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return indexed, converted


def _base_candidates(rules, kind):
    if rules is None:
        return None
    indexed, converted = rules
    row = indexed.get(kind)
    if type(row) is not dict or type(row.get('produce')) is not list or len(row['produce']) > 64:
        return None
    output = []
    for entry in row['produce']:
        try:
            if type(entry) is not dict:
                raise LayoutError('Invalid local production configuration')
            resource = _token(entry['r'])
            q = _number(entry['q']) if 'q' in entry else None
            req = entry.get('req', {})
            if type(req) is not dict:
                raise LayoutError('Invalid local production requirement')
            building_rows = req.get('buildings', [])
            if type(building_rows) is not list or len(building_rows) > 64:
                raise LayoutError('Invalid local building requirement')
            buildings = [_token(item['building']) for item in building_rows]
            target = converted.get(resource)
            target = _token(target) if target is not None else None
            output.append({'resource': resource, 'configured_base_q': q,
                'converts_to': target, 'building_kinds': buildings,
                'additional_requirements_present': bool(set(req) - {'buildings'}),
                'requirement_status': 'not_evaluated',
                'rate_semantics': 'static_configuration_candidate_not_effective_rate'})
        except ERRORS:
            return None
    return output


def capture_labor(h, me, state, *, own_units, owned_zones=(), owned_buildings=(),
                  profile=None, identity=None, local_rules=None):
    """Capture own labor inputs and explicit unsupported quantities.

    own_units/owned_zones/owned_buildings are (pointer, already-read-record)
    pairs. A zone record may instead be its integer zone ID. Context lists must
    cover the caller's owned objects; ownership is rechecked here. Foreign
    objects are neither followed nor exported. Each optional read is isolated.
    This does not make the external read atomic; all rows include native clock
    bounds and observed mutations invalidate the affected row.
    """
    result = {'schema_version': 1, 'source': SOURCE, 'status': 'unknown',
        'hlboot_sha256': VERIFIED_HASH, 'data_cdb_sha256': VERIFIED_CDB_HASH,
        'local_player_entity_uid': None, 'game_time_seconds': None,
        'time_before': None, 'time_after': None, 'sample_span_game_seconds': None,
        'atomic': False, 'native_individual_rates_available': False,
        'actual_settled_amounts_available': False,
        'individual_rate_status': 'computed_getter_no_persistent_unit_cache',
        'player_context': {}, 'units': [], 'zones': [], 'buildings': [],
        'reason_codes': [], 'unknown_fields': []}
    for key in IDENTITIES:
        if identity and key in identity:
            result[key] = identity[key]
    def unknown(field, reason):
        if field not in result['unknown_fields']:
            result['unknown_fields'].append(field)
        if reason not in result['reason_codes']:
            result['reason_codes'].append(reason)
    def optional(pointer, field, validator, output, key):
        try:
            output[key] = _read_scalar(h, pointer, field, validator)
        except ERRORS:
            output[key] = None
            unknown('optional_native_input', 'labor_optional_input_unavailable')
    try:
        if profile is None:
            profile = json.loads((HERE.parent / 'runtime/build_profile.json').read_text(encoding='utf8'))
        if (type(profile) is not dict or profile.get('hlboot_sha256') != VERIFIED_HASH
                or profile.get('data_cdb_sha256') != VERIFIED_CDB_HASH):
            unknown('all', 'labor_unverified_build')
            return result
        if (h.object_type(me).name != 'ent.Player' or h.field(state, 'me') != me
                or h.field(me, 'aiJob')):
            unknown('all', 'labor_local_human_identity_unverified')
            return result
        result['local_player_entity_uid'] = _integer(h.field(me, '__uid'))
        if result['local_player_entity_uid'] == 0:
            raise LayoutError('Invalid local entity identity')
        start = _number(h.field(state, 'time'))
        if start < 0:
            raise LayoutError('Invalid native game clock')
        result['game_time_seconds'] = result['time_before'] = start
        rules = _rules(local_rules, profile)
        if rules is None:
            unknown('configuration_candidates', 'labor_local_rules_unavailable')
    except ERRORS:
        unknown('all', 'labor_identity_or_clock_unavailable')
        return result

    # Own technology and tools are materialized maps. They do not cover every
    # possible provider (Owl/Fountain/cloned/conquest/etc.) or give a final bonus.
    context = result['player_context']
    for field, key, validator in (('clan', 'clan', lambda p: _nullable_string(h, p)),):
        optional(me, field, validator, context, key)
    optional(state, 'inWinter', _bool, context, 'in_winter')
    for field, key, validator in (('res', 'resources', lambda p: p), ('tech', 'technology', lambda p: p),
                                   ('upgrades', 'upgrades', lambda p: p)):
        try:
            pointer = h.field(me, field)
            expected = {'res': 'ent.player.ResourcesComponent', 'tech': 'ent.player.TechComponent',
                        'upgrades': 'ent.player.UpgradesComponent'}[field]
            if not pointer or h.object_type(pointer).name != expected:
                raise LayoutError('Unsupported local component')
            if field == 'res':
                for native, exported, validate in (
                        ('currentHappiness', 'happiness_cache', _number),
                        ('coefCons', 'consumption_coefficient', _number),
                        ('population', 'population_cache', _integer),
                        ('overworking', 'overworking', _bool),
                        ('lastFeast', 'last_feast_time', _number),
                        ('lastOverwork', 'last_overwork_time', _number)):
                    optional(pointer, native, validate, context, exported)
            else:
                native = 'researchedTechs' if field == 'tech' else 'upgradedUnits'
                exported = 'researched_tech_entries' if field == 'tech' else 'upgraded_unit_entries'
                try:
                    context[exported] = _map(h, h.field(pointer, native))
                except ERRORS:
                    context[exported] = None
                    unknown(exported, 'labor_modifier_map_unavailable')
                if field == 'tech':
                    optional(pointer, 'fameLevel', _integer, context, 'fame_level')
                    try:
                        fountain = h.field(pointer, 'allFountainResearches')
                        context['special_tech_provider_present'] = bool(objects(h, fountain, limit=1024)) if fountain else False
                        context['owl_component_present'] = bool(h.field(me, 'owl'))
                    except ERRORS:
                        context['special_tech_provider_present'] = None
                        context['owl_component_present'] = None
                        unknown('special_tech_provider_present', 'labor_optional_input_unavailable')
        except ERRORS:
            unknown(key, 'labor_modifier_component_unavailable')
    try:
        context['effects'] = _effects(h, me)
    except ERRORS:
        context['effects'] = None
        unknown('player_effects', 'labor_effect_map_unavailable')

    zone_map, building_map = {}, {}
    if type(owned_zones) not in (tuple, list) or len(owned_zones) > 4096:
        owned_zones = ()
        unknown('zones', 'labor_context_scope_unverified')
    for pair in owned_zones:
        try:
            pointer, record = pair
            zid = record if type(record) is int else record['id'] if 'id' in record else record['zone_id']
            _integer(zid)
            if (h.object_type(pointer).name != 'ent.Zone' or h.field(pointer, 'owner') != me
                    or h.field(pointer, 'id') != zid or zid in zone_map):
                raise LayoutError('Unverified own zone')
            row = {'zone_id': zid}
            try:
                row['effects'] = _effects(h, pointer)
                row['flags'] = _flags(h, h.field(pointer, 'flags'))
            except ERRORS:
                row['effects'] = row['flags'] = None
                unknown('zones.optional_inputs', 'labor_optional_input_unavailable')
            if h.field(pointer, 'owner') != me:
                raise LayoutError('Own zone changed while reading')
            result['zones'].append(row)
            zone_map[zid] = pointer
        except ERRORS:
            unknown('zones', 'labor_non_owned_or_changed_context_omitted')

    if type(owned_buildings) not in (tuple, list) or len(owned_buildings) > 10000:
        owned_buildings = ()
        unknown('buildings', 'labor_context_scope_unverified')
    for pair in owned_buildings:
        try:
            pointer, record = pair
            uid, zid, kind = _integer(record['uid']), _integer(record['zone_id']), _token(record['kind'])
            zone = h.field(pointer, 'zone')
            if (zid not in zone_map or zone != zone_map[zid] or h.field(zone, 'owner') != me
                    or h.field(pointer, '__uid') != uid or uid in building_map
                    or _nullable_string(h, h.field(pointer, 'kind')) != kind):
                raise LayoutError('Unverified own building')
            signature = (zone, uid, kind, h.field(pointer, 'upgraded'))
            row = {'uid': uid, 'zone_id': zid, 'kind': kind}
            optional(pointer, 'upgraded', _integer, row, 'upgraded')
            optional(pointer, 'extended', _bool, row, 'extended')
            optional(pointer, 'hitLife', _number, row, 'hit_life')
            optional(pointer, 'waitRepair', _bool, row, 'wait_repair')
            try:
                row['effects'] = _effects(h, pointer)
            except ERRORS:
                row['effects'] = None
                unknown('building_effects', 'labor_effect_map_unavailable')
            row['harbor_produce_choice'] = None
            if h.object_type(pointer).name == 'ent.b.Harbor':
                optional(pointer, 'produceChoice', lambda p: _nullable_string(h, p), row, 'harbor_produce_choice')
                optional(pointer, 'forceStop', _bool, row, 'harbor_force_stop')
            # Mine production is credited by the resource deposit's shared
            # progress, never by a per-miner inventory counter.
            row['state_progress'] = row['state_contains'] = None
            try:
                materialized = h.field(pointer, 'state')
                if materialized:
                    optional(materialized, 'progress', lambda p: _boxed_nullable_float(h, p), row, 'state_progress')
                    optional(materialized, 'contains', lambda p: _boxed_nullable_float(h, p), row, 'state_contains')
            except ERRORS:
                unknown('building_state', 'labor_optional_input_unavailable')
            if ((h.field(pointer, 'zone'), h.field(pointer, '__uid'),
                 _nullable_string(h, h.field(pointer, 'kind')), h.field(pointer, 'upgraded')) != signature
                    or h.field(zone, 'owner') != me):
                raise LayoutError('Own building changed while reading')
            building_map[pointer] = row
            result['buildings'].append(row)
        except ERRORS:
            unknown('buildings', 'labor_non_owned_or_changed_context_omitted')

    if type(own_units) not in (tuple, list) or len(own_units) > 10000:
        unknown('units', 'labor_context_scope_unverified')
        own_units = ()
    seen = set()
    for pair in own_units:
        try:
            pointer, record = pair
            uid, kind = _integer(record['uid']), _token(record['kind'])
            if (h.field(pointer, 'owner') != me or h.field(pointer, '__uid') != uid
                    or _nullable_string(h, h.field(pointer, 'kind')) != kind or uid in seen):
                raise LayoutError('Unverified own unit')
            seen.add(uid)
            signature = (h.field(pointer, 'owner'), h.field(pointer, '__uid'), h.field(pointer, 'kind'),
                         h.field(pointer, 'zone'), h.field(pointer, 'assignTo'), h.field(pointer, 'produce'),
                         h.field(pointer, 'hasNoWork'), h.field(pointer, 'curJob'))
            live_zone_id = _integer(h.field(signature[3], 'id')) if signature[3] else None
            if record.get('zone_id') != live_zone_id:
                raise LayoutError('Own unit zone changed since parent read')
            row = {'uid': uid, 'kind': kind, 'zone_id': record.get('zone_id'),
                'native_actual_rate': None, 'native_settled_amount': None,
                'model_effective_rate': None,
                'rate_status': 'computed_getter_no_persistent_unit_cache',
                'can_produce': None, 'can_produce_status': 'getter_not_executed',
                'inputs': {}, 'effects': None, 'assignment': None,
                'configuration_candidates': _base_candidates(rules, kind)}
            inputs = row['inputs']
            for native, exported, validate in (
                    ('produce', 'produce', lambda p: _nullable_string(h, p)),
                    ('hasNoWork', 'has_no_work', _bool), ('justAssigned', 'just_assigned', _bool),
                    ('enabled', 'enabled', _bool), ('hitLife', 'hit_life', _number),
                    ('sickCounter', 'sick_counter', _number),
                    ('sickReason', 'sick_reason', lambda p: _nullable_string(h, p)),
                    ('status', 'status', lambda p: _nullable_string(h, p)),
                    ('statusTimer', 'status_timer', _number),
                    ('curJob', 'cur_job_kind', lambda p: _job_kind(h, p)),
                    ('assignedJob', 'assigned_job', lambda p: _nullable_string(h, p)),
                    ('flags', 'flags', lambda p: _flags(h, p))):
                optional(pointer, native, validate, inputs, exported)
            try:
                row['effects'] = _effects(h, pointer)
            except ERRORS:
                unknown('unit_effects', 'labor_effect_map_unavailable')
            assigned = building_map.get(h.field(pointer, 'assignTo'))
            if assigned is not None:
                row['assignment'] = {'building_uid': assigned['uid'], 'zone_id': assigned['zone_id'],
                    'same_zone': row['zone_id'] == assigned['zone_id']}
            else:
                row['assignment_status'] = ('no_assignment' if not h.field(pointer, 'assignTo')
                                            else 'non_owned_or_unverified_reference_omitted')
            current = (h.field(pointer, 'owner'), h.field(pointer, '__uid'), h.field(pointer, 'kind'),
                       h.field(pointer, 'zone'), h.field(pointer, 'assignTo'), h.field(pointer, 'produce'),
                       h.field(pointer, 'hasNoWork'), h.field(pointer, 'curJob'))
            if signature != current:
                raise LayoutError('Own unit changed during read')
            result['units'].append(row)
        except ERRORS:
            unknown('units', 'labor_non_owned_or_changed_unit_omitted')
    try:
        end = _number(h.field(state, 'time'))
        if end < start or h.field(state, 'me') != me or h.field(me, '__uid') != result['local_player_entity_uid']:
            raise LayoutError('Local labor identity or clock changed')
        result['time_after'] = end
        result['sample_span_game_seconds'] = end - start
    except ERRORS:
        # Do not retain potentially crossed-session materialized inputs.
        result.update(status='unknown', units=[], zones=[], buildings=[], player_context={})
        unknown('all', 'labor_identity_or_clock_changed_during_read')
        return result
    result['status'] = 'partial' if result['unknown_fields'] else 'ok'
    return result
