"""Strict pure whitelist for own persistent-operation observations.

No memory/process modules are imported. Unknown or malformed subcomponents stay
unknown; no source dictionary, runtime pointer, private path or error is copied.
"""
from __future__ import annotations

import math
import re

SOURCE = 'native_own_operation_state_read_only'
QUOTE_SOURCE = 'verified_native_getFeastCost_formula'
HLBOOT_SHA256 = 'db0459d251879698420f58f6819e0ab2f75a3cde6e65611f2ad723f3b4b7ef76'
DATA_CDB_SHA256 = '88d4c84b9622ce237533afc8674174aa9006d87a03258cd7e5fd671f930174e3'
IDENTITIES = ('session_key', 'capture_run_id', 'capture_segment_id')
RESOURCES = frozenset(('Food','Wood','Money','Stone','Iron','Lore','Faith','Prescience','Dominion'))
BOOL_FIELDS = frozenset(('instigator_is_self','team_leader_present','team_leader_is_self',
                         'has_relic_goat','has_relic_chalice','has_trial_faith'))
INT_FIELDS = frozenset(('feast_count','free_feast','free_feast_count','living_legend_stacks'))
NUMBER_FIELDS = frozenset(('last_feast','free_feast_time','goat_cost_reduction',
                           'hunt_cost_reduction','living_legend_cost_reduction'))
FEAST_FIELDS = BOOL_FIELDS | INT_FIELDS | NUMBER_FIELDS
QUOTE_INPUTS = ('feast_count','free_feast','free_feast_count','last_feast','free_feast_time',
    'team_leader_is_self','has_relic_goat','has_relic_chalice','has_trial_faith',
    'hunt_cost_reduction','goat_cost_reduction','living_legend_cost_reduction')
_TOKEN = re.compile(r'[A-Za-z][A-Za-z0-9_.]{0,127}\Z')
_IDENTITY = re.compile(r'[A-Za-z0-9_:-]{1,128}\Z')
_KNOWN_SOURCE_REASONS = frozenset((
    'unverified_build','unverified_local_human_player',
    'own_operation_identity_or_component_unavailable','own_feast_field_unavailable',
    'own_feast_team_source_unavailable','own_feast_modifier_unavailable',
    'own_special_tech_state_unavailable','own_colonization_price_map_unavailable',
    'own_colonization_modifier_unavailable','known_zone_colonization_state_unavailable',
    'own_building_ownership_not_verified','own_building_cost_unavailable',
    'own_building_state_unavailable','own_relic_active_state_not_proven_absent',
    'game_time_rolled_back_during_operation_read','operation_end_time_unavailable'))
_KNOWN_UNKNOWN_FIELDS = frozenset(('all','time_after','building_costs','building_costs.cost',
    'colonizations.paid_cost','colonizations.extra_paid_cost','colonizations.progress',
    'colonizations.cost_reduction')) | frozenset('feast.' + key for key in FEAST_FIELDS)


def _integer(value):
    return type(value) is int and 0 <= value < 2 ** 53


def _number(value):
    if type(value) is int:return abs(value) < 2 ** 53
    return type(value) is float and math.isfinite(value) and abs(value) < 2 ** 53


def _ids(value):
    return type(value) is list and len(value) <= 10000 and all(_integer(v) for v in value) and len(value) == len(set(value))


def _reject(reason):
    return {'schema_version':1,'source':SOURCE,'status':'rejected',
            'reason_codes':[reason],'unknown_fields':['all'],
            'atomic':False,'complete_transaction_ledger':False}


def _cost(value):
    if type(value) is not list or len(value) > 64:
        return None
    output=[]
    for item in value:
        if (type(item) is not dict or set(item) != {'resource','amount'}
                or type(item.get('resource')) is not str or item['resource'] not in RESOURCES
                or not _number(item.get('amount')) or item['amount'] < 0):
            return None
        output.append({'resource':item['resource'],'amount':item['amount']})
    return output


def _quote(value, feast, snapshot, component_status):
    """Revalidate both quote evidence and this supported formula branch."""
    unsupported={'status':'unsupported','source':QUOTE_SOURCE,'cost':[]}
    if type(value) is not dict or value.get('status') != 'verified':
        return unsupported
    if (component_status != 'ok' or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value.get('source') != QUOTE_SOURCE
            or value.get('build_evidence') != {'hlboot_sha256':HLBOOT_SHA256,'data_cdb_sha256':DATA_CDB_SHA256}
            or type(value.get('input_evidence')) is not dict
            or set(value['input_evidence']) != set(QUOTE_INPUTS)
            or any(value['input_evidence'][key] != feast.get(key)
                   or (type(value['input_evidence'][key]) is not bool if key in BOOL_FIELDS
                       else not _number(value['input_evidence'][key])) for key in QUOTE_INPUTS)
            or snapshot.get('clan') != 'Bear'):
        return unsupported
    context=snapshot.get('game_context')
    if (type(context) is not dict
            or context.get('mode') != {'kind':'Singleplayer','args':[{'kind':'FreeForAll','args':[]}]}
            or context.get('bifrost_data_present') is not False or context.get('editor_present') is not False):
        return unsupported
    if (any(not _integer(feast.get(k)) or feast[k] > 10000 for k in ('feast_count','free_feast','free_feast_count'))
            or any(not _number(feast.get(k)) for k in ('last_feast','free_feast_time',
                'hunt_cost_reduction','goat_cost_reduction','living_legend_cost_reduction'))
            or feast.get('team_leader_is_self') is not True
            or any(feast.get(k) is not False for k in ('has_relic_goat','has_relic_chalice','has_trial_faith'))
            or any(feast[k] != 0 for k in ('hunt_cost_reduction','goat_cost_reduction','living_legend_cost_reduction'))):
        return unsupported
    if feast['free_feast'] > 0:
        amount,branch=0,'observed_free_feast'
    else:
        try:
            tens=(10.0 * math.pow(1.5,feast['feast_count']) * 10.0) / 10.0
        except OverflowError:
            return unsupported
        if not math.isfinite(tens) or tens > 214748364:
            return unsupported
        amount,branch=math.floor(tens+.5)*10,'paid_feast_zero_modifiers'
    cost=_cost(value.get('cost'))
    if cost != [{'resource':'Food','amount':amount}] or value.get('formula_branch') != branch:
        return unsupported
    return {'status':'verified','source':QUOTE_SOURCE,'cost':cost,'formula_branch':branch}


def project_operations(value, snapshot):
    """Return a strictly normalized own component, or a data-free rejection.

    ``snapshot`` is the parent ActionProbe snapshot being projected. Callers
    must pass its complete own-building list and historical discovered IDs.
    """
    if (type(value) is not dict or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value.get('source') != SOURCE or value.get('status') not in ('ok','partial')
            or type(snapshot) is not dict or snapshot.get('status') != 'ok'):
        return _reject('invalid_operation_component')
    for key in IDENTITIES:
        if (type(snapshot.get(key)) is not str or not _IDENTITY.fullmatch(snapshot[key])
                or value.get(key) != snapshot[key]):
            return _reject('operation_capture_identity_mismatch')
    uid=snapshot.get('local_player_entity_uid')
    identity=snapshot.get('local_player_identity')
    if (not _integer(uid) or uid == 0 or not _integer(value.get('local_player_entity_uid'))
            or value['local_player_entity_uid'] != uid
            or type(identity) is not dict or identity.get('verified') is not True
            or identity.get('role') != 'player' or identity.get('entity_uid') != uid
            or identity.get('is_ai') is not False or identity.get('in_player_list') is not True):
        return _reject('operation_local_player_identity_unverified')
    if (not _ids(snapshot.get('owned_zone_ids')) or not _ids(snapshot.get('discovered_zone_ids'))
            or type(snapshot.get('buildings')) is not list or len(snapshot['buildings']) > 10000):
        return _reject('operation_parent_scope_unverified')
    when,parent_when=value.get('game_time_seconds'),snapshot.get('game_time_seconds')
    if not _number(when) or when < 0 or not _number(parent_when) or not -0.025 <= parent_when-when <= 5:
        return _reject('operation_game_clock_mismatch')
    owned=set(snapshot['owned_zone_ids']); known=owned | set(snapshot['discovered_zone_ids'])
    own_buildings={}
    for building in snapshot['buildings']:
        if (type(building) is not dict or not _integer(building.get('uid'))
                or not _integer(building.get('zone_id')) or building['zone_id'] not in owned
                or type(building.get('kind')) is not str or not _TOKEN.fullmatch(building['kind'])
                or building['uid'] in own_buildings):
            return _reject('operation_parent_buildings_unverified')
        for owner_key in ('owner_uid','owner_entity_uid','player_uid','player_entity_uid'):
            if owner_key in building and building[owner_key] != uid:
                return _reject('operation_parent_buildings_unverified')
        own_buildings[building['uid']]={'zone_id':building['zone_id'],'kind':building['kind']}
    result={'schema_version':1,'source':SOURCE,'status':value['status'],
            'local_player_entity_uid':uid,'game_time_seconds':when,
            'atomic':False,'complete_transaction_ledger':False,
            'feast':{},'colonizations':[],'building_costs':[],
            'unknown_fields':[],'reason_codes':[],**{k:snapshot[k] for k in IDENTITIES}}
    def unknown(field,reason):
        if field not in result['unknown_fields']:result['unknown_fields'].append(field)
        if reason not in result['reason_codes']:result['reason_codes'].append(reason)
        result['status']='partial'
    source_unknown=value.get('unknown_fields')
    if type(source_unknown) is list and len(source_unknown) <= 128:
        for field in source_unknown:
            if type(field) is str and field in _KNOWN_UNKNOWN_FIELDS:
                unknown(field,'source_operation_field_unavailable')
            else:unknown('source_diagnostics','unrecognized_operation_diagnostic_omitted')
    else:unknown('source_diagnostics','operation_diagnostics_unavailable')
    source_reasons=value.get('reason_codes')
    if type(source_reasons) is list and len(source_reasons) <= 128:
        for reason in source_reasons:
            if type(reason) is str and reason in _KNOWN_SOURCE_REASONS:
                if reason not in result['reason_codes']:result['reason_codes'].append(reason)
            else:unknown('source_diagnostics','unrecognized_operation_diagnostic_omitted')
    else:unknown('source_diagnostics','operation_diagnostics_unavailable')
    feast=value.get('feast')
    if type(feast) is not dict:feast={}
    for key in sorted(FEAST_FIELDS):
        item=feast.get(key)
        if key == 'instigator_is_self' and item is None:
            # Before the first feast the native instigator is legitimately null.
            # A source read failure still survives via source_unknown above.
            result['feast'][key] = None
            continue
        valid=(type(item) is bool if key in BOOL_FIELDS else _integer(item) if key in INT_FIELDS else _number(item))
        result['feast'][key]=item if valid else None
        if not valid:unknown('feast.'+key,'own_feast_field_unavailable')
    for key in ('time_before','time_after','sample_span_game_seconds'):
        item=value.get(key)
        if _number(item):result[key]=item
        else:unknown('operation_read_clock','operation_read_clock_unavailable')
    if all(k in result for k in ('time_before','time_after','sample_span_game_seconds')):
        before,after,span=(result[k] for k in ('time_before','time_after','sample_span_game_seconds'))
        if (before < 0 or abs(before-when) > .000001 or not 0 <= after-before <= 5
                or abs(after-before-span) > .000001 or after > parent_when+.025):
            return _reject('operation_read_clock_mismatch')
    rows=value.get('colonizations')
    if type(rows) is not list or len(rows) > 4096:
        rows=[];unknown('colonizations','own_colonization_state_unavailable')
    seen=set()
    for row in rows:
        if (type(row) is not dict or not _integer(row.get('zone_id'))
                or row['zone_id'] not in known or row['zone_id'] in seen):
            unknown('colonizations','non_known_or_ambiguous_colonization_omitted');continue
        seen.add(row['zone_id'])
        out={'zone_id':row['zone_id']}
        for key in ('progress','cost_reduction','started_by_self'):
            item=row.get(key)
            valid=type(item) is bool if key=='started_by_self' else _number(item)
            out[key]=item if valid else None
            if not valid and (item is not None or key=='progress'):
                unknown('colonizations.'+key,'own_colonization_field_unavailable')
        for key in ('paid_cost','extra_paid_cost'):
            status=row.get(key+'_record_status')
            costs=_cost(row.get(key))
            if status=='stored_record' and costs is not None:
                out[key],out[key+'_record_status']=costs,status
            elif status=='no_record' and row.get(key) is None:
                out[key],out[key+'_record_status']=None,status
            else:
                out[key],out[key+'_record_status']=None,'unavailable'
                unknown('colonizations.'+key,'own_colonization_cost_unavailable')
        result['colonizations'].append(out)
    rows=value.get('building_costs')
    if type(rows) is not list or len(rows) > 10000:
        rows=[];unknown('building_costs','own_building_cost_state_unavailable')
    seen=set()
    for row in rows:
        canonical=own_buildings.get(row.get('uid')) if type(row) is dict and _integer(row.get('uid')) else None
        if (canonical is None or not _integer(row.get('zone_id')) or row['zone_id'] != canonical['zone_id']
                or row.get('kind') != canonical['kind'] or row['uid'] in seen):
            unknown('building_costs','non_owned_or_ambiguous_building_cost_omitted');continue
        seen.add(row['uid'])
        out={'uid':row['uid'],'zone_id':canonical['zone_id'],'kind':canonical['kind'],
             'was_free':row.get('was_free') if type(row.get('was_free')) is bool else None,
             'cost':None,'cost_record_status':'unknown'}
        if out['was_free'] is None:unknown('building_costs.was_free','own_building_cost_unavailable')
        costs=_cost(row.get('cost'));status=row.get('cost_record_status')
        if costs is not None and status=='stored_record':
            out['cost'],out['cost_record_status']=costs,status
        elif row.get('cost') is None and status=='not_stored':
            out['cost_record_status']=status
        else:unknown('building_costs.cost','own_building_cost_unavailable')
        result['building_costs'].append(out)
    if 'feast_quote' in value:
        result['feast_quote']=_quote(value['feast_quote'],result['feast'],snapshot,result['status'])
    return result
