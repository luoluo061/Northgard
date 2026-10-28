"""Fail-closed validation of one local contribution or the repository data tree."""
from __future__ import annotations

import argparse
from collections import Counter,deque
import gzip
import io
import json
from pathlib import Path
import re
import stat
import struct
import zipfile
import zlib

from submission_contract import (BASE_RING_UNKNOWNS,DATA_FILES,FILES,MANIFEST_SCHEMA,MATCH_CONTEXT,MAX_COMPRESSED,MAX_EXPANDED,
    KNOWN_ZONE_SOURCE,KNOWN_ZONE_SCOPE,KNOWN_ZONE_HL,KNOWN_ZONE_CDB,KNOWN_ZONE_UNKNOWNS,KNOWN_ZONE_REASONS,
    MAX_RECORD,MAX_TOTAL_EXPANDED,RECORD_SCHEMAS,SUBMISSION_SCHEMA,VARIANT_LAYOUTS,SubmissionError,
    clock,digest,loads,mode_facts,observe_mode,resolve_mode,team_groups,validate)


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise SubmissionError('Only regular files are accepted')


def check_action_references(value,history):
    if type(value) is dict:
        for key,item in value.items():
            if key in ('uid','building_uid') and item is not None and item not in history['entities']:
                raise SubmissionError('Action reference outside own entity history')
            if key in ('zone_id','target_zone_id'):
                locations=item.values() if type(item) is dict else (item,)
                if any(location is not None and (type(location) is not int or location not in history['zones'])
                       for location in locations):
                    raise SubmissionError('Action location outside historical knowledge')
            if key=='assigned_unit_ids':
                pairs=item.values() if type(item) is dict else (item,)
                if any(ids is not None and not set(ids).issubset(history['units']) for ids in pairs):
                    raise SubmissionError('Action worker list outside own history')
            if key=='discovered_zone_ids' and type(item) is dict and item.get('added') not in history['zones']:
                raise SubmissionError('Action discovery outside own knowledge')
            check_action_references(item,history)
    elif type(value) is list:
        for item in value:
            check_action_references(item,history)


def member_data(path):
    """Bounded bytes, no extraction; ZIP names have an exact flat allowlist."""
    if path.is_dir():
        if path.is_symlink() or set(p.name for p in path.iterdir()) != set(FILES):
            raise SubmissionError('Unexpected contribution directory member')
        output={}
        for name in FILES:
            target=path/name
            regular(target)
            limit=MAX_COMPRESSED if name in DATA_FILES else MAX_RECORD
            if target.stat().st_size > limit:
                raise SubmissionError('Contribution member too large')
            output[name]=target.read_bytes()
        return output
    regular(path)
    if path.stat().st_size > MAX_COMPRESSED*len(DATA_FILES)+8*MAX_RECORD:
        raise SubmissionError('Review archive too large')
    with zipfile.ZipFile(path) as archive:
        entries=archive.infolist()
        if [e.filename for e in entries] != list(FILES) or archive.comment:
            raise SubmissionError('Unsafe, duplicate, or unexpected ZIP member')
        output={}
        cursor=0
        for entry in entries:
            if entry.flag_bits or stat.S_ISLNK(entry.external_attr>>16) or entry.extra or entry.comment:
                raise SubmissionError('Noncanonical, encrypted, symbolic or annotated ZIP member rejected')
            if entry.compress_type!=zipfile.ZIP_STORED or entry.header_offset!=cursor:
                raise SubmissionError('Hidden ZIP prefix/gap or outer compression rejected')
            with path.open('rb') as binary:
                binary.seek(cursor)
                header=binary.read(30)
                if len(header)!=30 or header[:4]!=b'PK\x03\x04':
                    raise SubmissionError('Invalid ZIP local header')
                fields=struct.unpack('<4s5H3I2H',header)
                filename_length,extra_length=fields[-2:]
                if extra_length or filename_length!=len(entry.filename) or binary.read(filename_length)!=entry.filename.encode('ascii'):
                    raise SubmissionError('ZIP local fields differ from canonical member')
                if fields[2] or fields[3] or fields[7]!=entry.compress_size or fields[8]!=entry.file_size:
                    raise SubmissionError('ZIP local sizes/flags differ from central directory')
            cursor+=30+len(entry.filename)+entry.file_size
            limit=MAX_COMPRESSED if entry.filename in DATA_FILES else MAX_RECORD
            if entry.file_size > limit or entry.compress_size > limit:
                raise SubmissionError('ZIP expanded size too large')
            with archive.open(entry) as handle:
                raw=handle.read(limit+1)
            if len(raw) > limit:
                raise SubmissionError('ZIP member exceeds size limit')
            output[entry.filename]=raw
        expected_size=cursor+sum(46+len(entry.filename) for entry in entries)+22
        if archive.start_dir!=cursor or path.stat().st_size!=expected_size:
            raise SubmissionError('Hidden ZIP central/trailing data rejected')
        return output


def validate_observation_scope(row):
    if row.get('status') != 'ok':
        if set(row)-{'status','sequence','game_time_seconds','game_clock','session_key','capture_run_id','capture_segment_id'}:
            raise SubmissionError('Rejected observation carries game data')
        return
    if row.get('observation_type') != 'PlayerObservation' or row.get('scope') != 'local_player_and_owned_zones':
        raise SubmissionError('Observation scope is not the own-player contract')
    required={'status','observation_type','scope','local_player_entity_uid','game_time_seconds','session_key',
              'capture_run_id','capture_segment_id','clan','stocks','owned_zone_ids','discovered_zone_ids','units','buildings'}
    if required-set(row) or row['local_player_entity_uid']<=0:
        raise SubmissionError('Accepted player observation is missing own-state provenance')
    if any(len(row[key])!=len(set(row[key])) for key in ('owned_zone_ids','discovered_zone_ids')):
        raise SubmissionError('Duplicate known/owned zone identifier')
    own=set(row.get('owned_zone_ids',[]));known=own|set(row.get('discovered_zone_ids',[]))
    units=row.get('units',[]); buildings=row.get('buildings',[])
    ids=[v['uid'] for v in units+buildings]
    if len(ids) != len(set(ids)):
        raise SubmissionError('Duplicate entity UID')
    entity_ids=set(ids)
    unit_by_id={u['uid']:u for u in units};building_by_id={b['uid']:b for b in buildings}
    for building in buildings:
        if building['zone_id'] not in own or not set(building.get('assigned_unit_ids',[])).issubset({u['uid'] for u in units}):
            raise SubmissionError('Building outside owned scope')
    for unit in units:
        if unit.get('zone_id') is not None and unit['zone_id'] not in known:
            raise SubmissionError('Unit location outside historical known scope')
        for key in ('assign_to','previous_assign_to','going_to'):
            ref=unit.get(key)
            if ref and ((ref.get('uid') is not None and ref['uid'] not in entity_ids)
                        or (ref.get('zone_id') is not None and ref['zone_id'] not in known)):
                raise SubmissionError('Entity reference outside own/known scope')
    for event in row.get('own_events',[]):
        if event.get('actor') != 'self' or ('zone_id' in event and event['zone_id'] not in known):
            raise SubmissionError('Native event outside own scope')
    if 'known_zone_details' in row:
        validate_known_zone_component(row['known_zone_details'],row)
    for component_name in ('economy','labor','population_growth','operations','map_observation','match_context'):
        component=row.get(component_name)
        if not component:
            continue
        if component.get('status') not in ('ok','partial','read_race'):
            diagnostic_keys={'schema_version','status','source','scope','unknown_fields','reason_codes','atomic',
                'native_individual_rates_available','actual_settled_amounts_available','individual_rate_status','complete_transaction_ledger'}
            if set(component)-diagnostic_keys:
                raise SubmissionError('Unavailable component carries game payload')
            if component_name=='match_context' and component.get('scope')!='no_match_context_data':
                raise SubmissionError('Rejected match context must be data-free')
            continue
        required={'schema_version','status','source','session_key','capture_run_id','capture_segment_id','game_time_seconds'}
        if component_name!='economy':
            required.add('local_player_entity_uid')
        if required-set(component):
            raise SubmissionError('Active component is missing source/identity/clock')
        if any(component.get(key)!=row.get(key) for key in ('session_key','capture_run_id','capture_segment_id')):
            raise SubmissionError('Component identity differs from parent')
        if ('local_player_entity_uid' in component
                and component['local_player_entity_uid']!=row.get('local_player_entity_uid')):
            raise SubmissionError('Component local player differs from parent')
        if component_name=='labor':
            for unit in component.get('units',[]):
                canonical=unit_by_id.get(unit['uid'])
                if canonical is None or unit['kind']!=canonical['kind'] or unit['zone_id']!=canonical.get('zone_id'):
                    raise SubmissionError('Labor unit outside parent own scope')
                assignment=unit.get('assignment')
                if assignment:
                    target=building_by_id.get(assignment['building_uid'])
                    if target is None or assignment['zone_id']!=target['zone_id']:
                        raise SubmissionError('Labor assignment outside own buildings')
            for building in component.get('buildings',[]):
                canonical=building_by_id.get(building['uid'])
                if canonical is None or building['kind']!=canonical['kind'] or building['zone_id']!=canonical['zone_id']:
                    raise SubmissionError('Labor building outside own scope')
            if any(z['zone_id'] not in own for z in component.get('zones',[])):
                raise SubmissionError('Labor zone outside own scope')
        elif component_name=='population_growth':
            for hall in component.get('town_halls',[]):
                canonical=building_by_id.get(hall['uid'])
                if canonical is None or canonical['kind']!='TownHall' or hall['zone_id']!=canonical['zone_id']:
                    raise SubmissionError('Growth hall outside own scope')
            halls={hall['uid'] for hall in component.get('town_halls',[])}
            for interval in component.get('observed_progress_intervals',[]):
                if interval['town_hall_uid'] not in halls or any(interval[key]!=row.get(key) for key in ('session_key','capture_run_id','capture_segment_id')):
                    raise SubmissionError('Growth interval outside own hall/segment scope')
                if not interval['start_game_seconds']<interval['end_game_seconds']<=component['game_time_seconds']:
                    raise SubmissionError('Growth interval has invalid bounds')
        elif component_name=='map_observation':
            if component.get('map_identity',{}).get('map_instance_id')!=row.get('session_key'):
                raise SubmissionError('Map instance differs from parent')
            for zone in component.get('known_zones',[]):
                if not {'id','known_geometric_neighbor_ids'}.issubset(zone):
                    raise SubmissionError('Map cell is missing its identifier or adjacency')
                if zone['id'] not in known or not set(zone['known_geometric_neighbor_ids']).issubset(known):
                    raise SubmissionError('Map cell outside historical knowledge')
        elif component_name=='operations':
            if any(z['zone_id'] not in known for z in component.get('colonizations',[])):
                raise SubmissionError('Colonization outside historical knowledge')
            for building in component.get('building_costs',[]):
                canonical=building_by_id.get(building['uid'])
                if canonical is None or building['zone_id']!=canonical['zone_id'] or building['kind']!=canonical['kind']:
                    raise SubmissionError('Cost record outside own building scope')
        elif component_name=='match_context':
            validate_match_context(component,row)


def validate_known_zone_component(component,row):
    """Independent semantics check; never imports the candidate map reader."""
    def require(condition):
        if not condition:
            raise SubmissionError('Invalid discovered natural-details component')
    diagnostic={'schema_version','status','source','scope','zones','reason_codes','unknown_fields'}
    if component.get('status')!='partial':
        expected={'unsupported_context':'known_zone_context_unsupported',
                  'unavailable':'known_zone_component_unavailable','rejected':'invalid_known_zone_component'}
        require(set(component)==diagnostic and component.get('source')==KNOWN_ZONE_SOURCE
                and component.get('scope')=='no_known_zone_details' and component.get('zones')==[]
                and component.get('unknown_fields')==['known_zone_details']
                and component.get('status') in expected
                and component.get('reason_codes')==[expected[component['status']]])
        return
    keys={'schema_version','status','source','scope','hlboot_sha256','data_cdb_sha256',
          'local_player_entity_uid','session_key','capture_run_id','capture_segment_id',
          'game_time_seconds','time_before','time_after','sample_span_game_seconds','atomic',
          'zones','reason_codes','unknown_fields'}
    require(set(component)==keys and component['source']==KNOWN_ZONE_SOURCE and component['scope']==KNOWN_ZONE_SCOPE
            and component['hlboot_sha256']==KNOWN_ZONE_HL and component['data_cdb_sha256']==KNOWN_ZONE_CDB
            and component['unknown_fields']==KNOWN_ZONE_UNKNOWNS and component['atomic'] is False
            and all(component[key]==row.get(key) for key in
                    ('local_player_entity_uid','session_key','capture_run_id','capture_segment_id')))
    context=row.get('game_context',{})
    require(context.get('mode_kind')=='Singleplayer' and context.get('mode_variant_kind')=='FreeForAll'
            and context.get('game_type_kind')=='Standard'
            and context.get('editor_present') is False and context.get('bifrost_data_present') is False)
    import math
    require(component['time_after']>=component['time_before']
            and component['game_time_seconds']==component['time_after']
            and math.isclose(component['time_after']-component['time_before'],component['sample_span_game_seconds'],abs_tol=1e-9)
            and component['sample_span_game_seconds']<=5
            and -0.025<=row['game_time_seconds']-component['time_after']<=5)
    require(len(component['reason_codes'])==len(set(component['reason_codes']))
            and set(component['reason_codes']).issubset(KNOWN_ZONE_REASONS))
    known=set(row.get('discovered_zone_ids',[]))
    require(len(component['zones'])==len(known)
            and {zone['zone_id'] for zone in component['zones']}==known)
    specs={
        'RuneStone':(['Lore','Faith'],1,[],None,None,'production_opportunity'),
        'CircleStone':(['Lore','Faith'],2,[('Lore',1.1)],None,None,'production_opportunity'),
        'Fertility':(['Wheat'],0,[],None,None,'production_opportunity'),
        'Stones':(['Stone'],None,[],None,None,'finite_mineral'),
        'IronDeposit':(['Iron'],None,[],None,None,'finite_mineral'),
        'Shoal':(['Fish'],0,[],None,None,'production_opportunity'),
        'Grove':(['Wood'],0,[('Wood',1.1)],None,None,'production_opportunity'),
        'DeerSpawner':(['Meat'],0,[],7,10,'renewing_deer_source')}
    seen_deposits=set()
    own_buildings={b['uid']:b for b in row.get('buildings',[])}
    own_unit_ids={u['uid'] for u in row.get('units',[])}
    for zone in component['zones']:
        for field in ('resource_reason_codes','wildlife_reason_codes'):
            require(len(zone[field])==len(set(zone[field])))
        if zone['natural_attributes_status']=='complete':
            require(type(zone['tall_grass_present']) is bool and type(zone['hiding_grass_present']) is bool)
        else:
            require(zone['tall_grass_present'] is None and zone['hiding_grass_present'] is None)
        if zone['wildlife_status']=='ok':
            require(zone['natural_attributes_status']=='complete'
                    and zone['tall_grass_present'] is False and zone['hiding_grass_present'] is False)
        require(zone['resources_status']!='unavailable' or zone['resources']==[])
        require(zone['wildlife_status']!='unavailable' or zone['wildlife']==[])
        require(len({r['kind'] for r in zone['resources']})==len(zone['resources'])
                and len({c['kind'] for c in zone['wildlife']})==len(zone['wildlife']))
        for resource in zone['resources']:
            kind=resource['kind'];channels,slots,multipliers,deer_max,deer_speed,depletion=specs[kind]
            reference={'semantics':'verified_default_configuration_not_effective_yield',
                'production_channels':channels,'fixed_assignment_slots':slots,
                'local_multiplier_reference':[{'resource':r,'multiplier':v,'scope':'ordinary_viking'} for r,v in multipliers],
                'deer_spawn_max':deer_max,'deer_spawn_speed_config':deer_speed,'depletion_model':depletion}
            require(resource['reference_properties']==reference)
            deposits=resource['deposits']
            if kind not in ('Stones','IronDeposit'):
                require(deposits==[] and resource['remaining_total_raw'] is None
                        and resource['modeled_ui_remaining_total'] is None)
                continue
            require(len(deposits)==resource['object_count'])
            for deposit in deposits:
                uid=deposit['natural_object_uid']
                require(uid not in seen_deposits and uid not in own_unit_ids)
                seen_deposits.add(uid)
                if uid in own_buildings:
                    require(own_buildings[uid]['kind']==kind and own_buildings[uid]['zone_id']==zone['zone_id'])
            values=[d['remaining_contains_raw'] for d in deposits]
            if zone['resources_status']=='ok' and all(value is not None for value in values):
                total=sum(values)
                require(resource['remaining_total_raw']==total and type(resource['remaining_total_raw']) in (int,float)
                        and resource['modeled_ui_remaining_total']==math.ceil(total))
            else:
                require(resource['remaining_total_raw'] is None and resource['modeled_ui_remaining_total'] is None)
        if zone['wildlife_status']!='ok':
            require(all(creature['count']>0 for creature in zone['wildlife']))


def _base_empty(reason):
    return {'schema_version':1,'policy_version':'base-neighborhood.v1','status':'unknown',
        'source':'derived_known_geometric_map_adjacency','scope':'local_player_known_base_neighborhood',
        'adjacency_semantics':'geometric_map_cell_neighbors_not_traversability',
        'distance_semantics':'shortest_path_in_current_known_reciprocal_graph','coverage':'incomplete',
        'coverage_semantics':'empty_rings_mean_not_observed_not_absent','anchor':None,
        'ring0_zone_ids':[],'ring1_zone_ids':[],'ring2_zone_ids':[],
        'known_unclassified_zone_ids':[],'zones':[],'reason_codes':[reason],
        'unknown_fields':list(BASE_RING_UNKNOWNS),'continuity':'current_sample_only'}


def _base_entities(row):
    """Recheck source references independently of the candidate collector."""
    known=set(row['discovered_zone_ids']);owned=set(row['owned_zone_ids'])
    entities={'units':[],'buildings':[]};reasons=set();candidates=[]
    for field in entities:
        for entity in row[field]:
            if (not 0<entity['uid']<2**53 or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.]{0,95}',entity['kind'])
                    or type(entity.get('zone_id')) is not int or not 0<=entity['zone_id']<2**31
                    or entity['zone_id'] not in known
                    or field=='buildings' and entity['zone_id'] not in owned):
                reasons.add('unverified_entity_reference_omitted')
            else:
                candidates.append((field,entity))
    counts=Counter(entity['uid'] for _,entity in candidates)
    for field,entity in candidates:
        if counts[entity['uid']]!=1:
            reasons.add('duplicate_own_entity_uid_omitted')
        else:
            entities[field].append(entity)
    return entities,reasons


def _base_graph(row):
    component=row.get('map_observation',{});keys=('session_key','capture_run_id','capture_segment_id')
    if (component.get('schema_version')!=1 or component.get('status')!='ok'
            or component.get('policy_version')!='player-map.v1'
            or component.get('source')!='native_discovered_static_map_read_only'
            or component.get('scope')!='local_player_discovered_static_cells'
            or component.get('adjacency_semantics')!='geometric_map_cell_neighbors_not_traversability'
            or component.get('hlboot_sha256')!='db0459d251879698420f58f6819e0ab2f75a3cde6e65611f2ad723f3b4b7ef76'
            or component.get('data_cdb_sha256')!='88d4c84b9622ce237533afc8674174aa9006d87a03258cd7e5fd671f930174e3'
            or any(component.get(key)!=row[key] for key in keys)
            or component.get('local_player_entity_uid')!=row['local_player_entity_uid']
            or component.get('map_identity',{}).get('map_instance_id')!=row['session_key']
            or component.get('game_time_seconds') is None
            or not -.025<=row['game_time_seconds']-component['game_time_seconds']<=5
            or type(component.get('known_zones')) is not list):
        return {},{},None,{'known_map_unavailable_or_unverified'}
    known=set(row['discovered_zone_ids']);rows={};duplicates=set();reasons=set()
    for zone in component['known_zones']:
        zid=zone.get('id')
        if type(zid) is not int or not 0<=zid<2**31 or zid not in known:
            reasons.add('unverified_map_zone_omitted')
        elif zid in rows:
            duplicates.add(zid);reasons.add('duplicate_map_zone_omitted')
        else:
            rows[zid]=zone
    for zid in duplicates:
        del rows[zid]
    if set(rows)!=known:
        reasons.add('known_map_rows_incomplete')
    directed={zid:set() for zid in rows}
    for zid,zone in rows.items():
        neighbors=zone.get('known_geometric_neighbor_ids')
        if type(neighbors) is not list:
            reasons.add('invalid_geometric_edges_omitted')
            continue
        for neighbor in neighbors:
            if not 0<=neighbor<2**31 or neighbor==zid or neighbor not in rows:
                reasons.add('invalid_geometric_edges_omitted')
            else:
                if neighbor in directed[zid]:
                    reasons.add('duplicate_geometric_edges_deduplicated')
                directed[zid].add(neighbor)
    graph={zid:set() for zid in rows}
    for zid,neighbors in directed.items():
        for neighbor in neighbors:
            if zid in directed[neighbor]:
                graph[zid].add(neighbor)
            else:
                reasons.add('asymmetric_geometric_edges_omitted')
    return rows,graph,component['game_time_seconds'],reasons


class BaseNeighborhoodValidation:
    """Frozen semantic check; never imports or trusts collector derivation code.

    Only an observed unique own completed TownHall establishes the session
    anchor. Historical anchors retain no graph or entity payload. The optional
    component can be absent from legacy frames; current data is always checked
    against the same containing PlayerObservation.
    """
    def __init__(self):
        self.context=None
        self.anchor=None

    def check(self,row):
        if 'base_neighborhood' not in row:
            return
        component=row['base_neighborhood']
        # Parent quality diagnostics are intentionally excluded from shared
        # records. A data-free unavailable result makes no spatial claim.
        unavailable=_base_empty('verified_player_observation_unavailable')
        if component==unavailable:
            return
        if (row.get('status')!='ok' or row.get('schema_version')!=1
                or row.get('policy_version')!='player-observation.v1'
                or not set(row['owned_zone_ids']).issubset(row['discovered_zone_ids'])
                or any(not 0<=zid<2**31 for key in ('owned_zone_ids','discovered_zone_ids') for zid in row[key])):
            raise SubmissionError('Base neighborhood requires verified own-player provenance')
        context=(row['session_key'],row['local_player_entity_uid'])
        if context!=self.context:
            self.context=context;self.anchor=None
        entities,reasons=_base_entities(row)
        if self.anchor is None:
            halls=[entity for entity in entities['buildings']
                   if entity['kind']=='TownHall' and entity.get('is_construction') is False]
            if len(halls)==1:
                self.anchor={**{key:halls[0][key] for key in ('zone_id','uid')},
                             'first_time':row['game_time_seconds']}
            else:
                reasons.add('own_completed_town_hall_ambiguous' if halls else 'own_completed_town_hall_unavailable')
        anchor=self.anchor
        if anchor is not None and anchor['first_time']>row['game_time_seconds']:
            anchor=None;reasons.add('base_anchor_unverified_or_outside_current_clock')
        rows,graph,map_when,map_reasons=_base_graph(row);reasons|=map_reasons
        expected=_base_empty('known_graph_only_complete_rings_unverified')
        expected.update({key:row[key] for key in ('session_key','capture_run_id','capture_segment_id',
                                                 'local_player_entity_uid','game_time_seconds')})
        expected['map_observed_game_time_seconds']=map_when
        paths={}
        if anchor is not None:
            zid=anchor['zone_id']
            hall=next((entity for entity in entities['buildings'] if entity['uid']==anchor['uid']
                       and entity['kind']=='TownHall' and entity['zone_id']==zid
                       and entity.get('is_construction') is False),None)
            expected['anchor']={'selection':'first_observed_own_town_hall','policy':'fixed_within_session',
                'original_spawn_status':'unverified','zone_id':zid if zid in rows else None,
                'town_hall_uid':hall['uid'] if hall else None,
                'town_hall_reference_status':'observed_own_completed' if hall else 'not_currently_observed',
                'first_observed_game_time_seconds':anchor['first_time']}
            if hall is None:
                reasons.add('anchor_town_hall_not_currently_observed')
            if zid not in rows:
                reasons.add('anchor_zone_not_in_current_known_map')
            else:
                paths[zid]=[zid];queue=deque([zid]);expected['status']='partial'
                while queue:
                    current=queue.popleft()
                    if len(paths[current])==3:
                        continue
                    for neighbor in sorted(graph[current]):
                        if neighbor not in paths:
                            paths[neighbor]=paths[current]+[neighbor];queue.append(neighbor)
        for ring in range(3):
            expected['ring%d_zone_ids'%ring]=sorted(zid for zid,path in paths.items() if len(path)-1==ring)
        expected['known_unclassified_zone_ids']=sorted(set(rows)-paths.keys())
        for zid in sorted(rows):
            path=paths.get(zid)
            expected['zones'].append({'zone_id':zid,'exploration':'known','current_visibility':'unverified',
                'ownership':'self' if zid in row['owned_zone_ids'] else 'unknown',
                'observed_graph_distance':len(path)-1 if path else None,
                'witness_path_zone_ids':path if path else [],
                'own_unit_uids':sorted(entity['uid'] for entity in entities['units'] if entity['zone_id']==zid),
                'own_building_uids':sorted(entity['uid'] for entity in entities['buildings'] if entity['zone_id']==zid),
                'map_observed_game_time_seconds':map_when,
                'own_entities_observed_game_time_seconds':row['game_time_seconds']})
        expected['reason_codes']=sorted(set(expected['reason_codes'])|reasons)
        # Continuity is a diagnostic label; it never permits carrying forward
        # geometry, ownership, or entity references from a different sample.
        expected['continuity']=component['continuity']
        if component!=expected:
            raise SubmissionError('Base neighborhood differs from same-frame known map/own references or fixed session anchor')


def validate_match_context(component,row):
    if component['scope']!='match_roster_counts_and_local_team':
        raise SubmissionError('Active match context scope differs from native roster contract')
    if set(MATCH_CONTEXT['properties'])-set(component):
        raise SubmissionError('Active match context is missing its fixed native fields')
    before,after,when,span=(component[key] for key in ('time_before','time_after','game_time_seconds','sample_span_game_seconds'))
    if (abs(before-when)>1e-7 or not 0<=after-before<=5 or abs(span-(after-before))>1e-7 or
            not -.025<=row['game_time_seconds']-after<=5):
        raise SubmissionError('Match context game clock differs from parent')
    context=row.get('game_context',{})
    if ((component['native_mode_kind'] in ('Multiplayer','Singleplayer') and component['native_mode_variant'] is None) or
            (component['native_mode_variant'] is not None and component['native_mode_kind'] not in ('Multiplayer','Singleplayer'))):
        raise SubmissionError('Native play mode and variant are inconsistent')
    for first,second in (('mode_kind','native_mode_kind'),('mode_variant_kind','native_mode_variant'),('game_type_kind','game_type_kind')):
        if context.get(first) is not None and component.get(second) is not None and context[first]!=component[second]:
            raise SubmissionError('Native match context conflicts with game context')
    if component['configured_team_layout']!=VARIANT_LAYOUTS.get(component['native_mode_variant']):
        raise SubmissionError('Configured team layout differs from native mode variant')
    count,teams,own,others,ordinal=(component[key] for key in ('native_player_count','native_team_count',
        'native_own_team_size','native_other_team_sizes','native_local_team_id'))
    if all(value is None for value in (count,teams,own,others,ordinal)):
        if (component['team_layout'] is not None or component['team_layout_status']!='unavailable' or
                component['configured_layout_matches_roster'] is not None):
            raise SubmissionError('Missing native roster cannot claim a verified team layout')
        return
    if (any(value is None for value in (count,teams,own,others,ordinal)) or len(others)+1!=teams or
            sum(others)+own!=count or not ordinal<teams or
            component['native_current_player_count'] is not None and component['native_current_player_count']>count):
        raise SubmissionError('Native match roster counts disagree')
    layout='v'.join(str(value) for value in sorted([own,*others],reverse=True))
    configured=component['configured_team_layout']
    agrees=layout==configured if configured is not None else None
    if component['configured_layout_matches_roster']!=agrees:
        raise SubmissionError('Native configured/roster agreement marker is incorrect')
    if agrees is False:
        if component['team_layout'] is not None or component['team_layout_status']!='unavailable':
            raise SubmissionError('Conflicting native roster/configuration cannot claim verified layout')
    elif component['team_layout']!=layout or component['team_layout_status']!='verified_native_roster':
        raise SubmissionError('Native roster layout differs from aggregate team sizes')


def validate_gzip_wrapper(raw):
    """A single canonical gzip member: no hidden filename/comment/timestamp."""
    if len(raw)<18 or raw[:4]!=b'\x1f\x8b\x08\x00' or raw[4:8]!=b'\0\0\0\0':
        raise SubmissionError('Noncanonical GZIP header: regenerate with the export tool')
    decoder=zlib.decompressobj(wbits=31)
    expanded=0
    try:
        for offset in range(0,len(raw),65536):
            pending=raw[offset:offset+65536]
            while pending:
                output=decoder.decompress(pending,65536)
                expanded+=len(output)
                if expanded>MAX_EXPANDED:
                    raise SubmissionError('GZIP expanded file limit exceeded')
                pending=decoder.unconsumed_tail
                if decoder.eof:
                    if decoder.unused_data or offset+65536<len(raw):
                        raise SubmissionError('Trailing or concatenated GZIP members rejected')
                    break
        if not decoder.eof:
            raise SubmissionError('Truncated GZIP member')
    except zlib.error as error:
        raise SubmissionError('Invalid GZIP member') from error


def validate_submission(path):
    path=Path(path)
    payload=member_data(path)
    submission=loads(payload['submission.json'])
    validate(submission,SUBMISSION_SCHEMA)
    shared=loads(payload['capture-manifest.json']);validate(shared,MANIFEST_SCHEMA)
    validate(payload['SUMMARY.md'].decode('utf-8'),{'type':'string','maxLength':MAX_RECORD})
    if shared['collector_version'] != submission['collector_version'] or shared['source_collection_id'] != submission['capture_id']:
        raise SubmissionError('Capture/submission manifests disagree')
    checksum_lines=payload['checksums.sha256'].decode('utf-8').splitlines()
    expected={name:digest(raw) for name,raw in payload.items() if name!='checksums.sha256'}
    observed={}
    for line in checksum_lines:
        match=re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9._-]+)',line)
        if not match or match[2] in observed:
            raise SubmissionError('Invalid checksum file')
        observed[match[2]]=match[1]
    if expected != observed:
        raise SubmissionError('Checksum mismatch')
    file_entries={e['path']:e for e in submission['files']}
    if len(file_entries)!=6 or set(file_entries)!=set(DATA_FILES)|{'capture-manifest.json','SUMMARY.md'}:
        raise SubmissionError('Invalid file manifest')
    total=0; player_times=[];observed_clans=set();observed=mode_facts();last_times={};histories={}
    base_neighborhoods=BaseNeighborhoodValidation()
    for name in (*DATA_FILES,'capture-manifest.json','SUMMARY.md'):
        entry=file_entries[name]
        raw=payload[name]
        if entry['size_bytes']!=len(raw) or entry['sha256']!=digest(raw):
            raise SubmissionError('File manifest mismatch')
        expanded=count=0
        if name in DATA_FILES:
            validate_gzip_wrapper(raw)
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(raw),mode='rb') as handle:
                    while line:=handle.readline(MAX_RECORD+1):
                        expanded+=len(line);total+=len(line)
                        if len(line)>MAX_RECORD or expanded>MAX_EXPANDED or total>MAX_TOTAL_EXPANDED:
                            raise SubmissionError('GZIP expanded record/data limit exceeded')
                        if not line.strip():
                            raise SubmissionError('Blank NDJSON record')
                        row=loads(line);validate(row,RECORD_SCHEMAS[name]);count+=1
                        when=row.get('game_time_seconds')
                        if when is not None and row.get('game_clock') != clock(when):
                            raise SubmissionError('Game calendar disagrees with native clock')
                        if row.get('session_key') not in (None,submission['source_session']):
                            raise SubmissionError('Multiple sessions in one match')
                        economy=row.get('economy')
                        if economy and economy.get('status') in ('ok','read_race'):
                            if any(economy.get(key)!=row.get(key) for key in ('session_key','capture_run_id','capture_segment_id')):
                                raise SubmissionError('Economic cache identity differs from containing sample')
                        if name.startswith('player-'):
                            validate_observation_scope(row)
                            base_neighborhoods.check(row)
                            if row.get('status')=='ok':
                                match_context=row.get('match_context',{})
                                if match_context.get('status') in ('ok','partial'):
                                    for key,expected_hash in submission['game_fingerprint'].items():
                                        if expected_hash is not None and match_context[key]!=expected_hash:
                                            raise SubmissionError('Native match context fingerprint differs from submission')
                                known_details=row.get('known_zone_details',{})
                                if known_details.get('status')=='partial':
                                    for key,expected_hash in submission['game_fingerprint'].items():
                                        if expected_hash is not None and known_details[key]!=expected_hash:
                                            raise SubmissionError('Known-zone details fingerprint differs from submission')
                                if when is None:
                                    raise SubmissionError('Missing player clock')
                                identity_key=tuple(row.get(key) for key in ('session_key','capture_run_id','capture_segment_id'))
                                if None in identity_key:
                                    raise SubmissionError('Missing accepted player capture identity')
                                if identity_key in last_times and when<last_times[identity_key]:
                                    raise SubmissionError('Game clock rollback inside the same segment')
                                last_times[identity_key]=when
                                history=histories.setdefault(identity_key,{'entities':set(),'units':set(),'zones':set()})
                                history['entities'].update(v['uid'] for v in row.get('units',[])+row.get('buildings',[]))
                                history['units'].update(v['uid'] for v in row.get('units',[]))
                                history['zones'].update(row.get('owned_zone_ids',[])+row.get('discovered_zone_ids',[]))
                                player_times.append(when);observed_clans.add(row.get('clan'))
                                observe_mode(observed,row)
                        elif name=='actions.ndjson.gz':
                            parent_identity=tuple(row.get(key) for key in ('session_key','capture_run_id','capture_segment_id'))
                            has_payload=bool(row.get('events') or row.get('resource_changes') or row.get('labor_transitions')
                                or row.get('record_kind') in ('baseline','interval','labor_transition_tail'))
                            if has_payload and (None in parent_identity or parent_identity not in histories or when is None):
                                raise SubmissionError('Action payload requires its own observed segment and game clock')
                            for event in row.get('events',[]):
                                history=histories.get(parent_identity)
                                if history is None:
                                    raise SubmissionError('Action interval has no own-player source segment')
                                for field in ('entity','resulting_building'):
                                    entity=event.get(field)
                                    if entity and entity.get('uid') not in history['entities']:
                                        raise SubmissionError('Action entity outside own-player history')
                                check_action_references(event,history)
                            for transition in row.get('labor_transitions',[]):
                                identity_key=tuple(transition[key] for key in ('session_key','capture_run_id','capture_segment_id'))
                                history=histories.get(identity_key)
                                if history is None or transition['unit_uid'] not in history['units']:
                                    raise SubmissionError('Labor transition outside own-player history')
                                if None not in parent_identity and identity_key!=parent_identity:
                                    raise SubmissionError('Labor transition differs from containing segment')
                                for key in ('assignment_observed_window','milestone_observed_window'):
                                    bounds=transition[key]
                                    if (bounds['previous_game_seconds']>bounds['observed_by_game_seconds'] or
                                        abs(bounds['sample_interval_game_seconds']-(bounds['observed_by_game_seconds']-bounds['previous_game_seconds']))>1e-7):
                                        raise SubmissionError('Invalid labor milestone window')
                                elapsed=transition['elapsed_from_assignment_game_seconds_bounds']
                                if elapsed is not None and elapsed['lower']>elapsed['upper']:
                                    raise SubmissionError('Invalid labor elapsed uncertainty bounds')
                                check_action_references(transition,history)
            except (OSError,EOFError) as error:
                raise SubmissionError('Invalid compressed stream') from error
        else:
            expanded,count=len(raw),1
        if expanded!=entry['expanded_bytes'] or count!=entry['record_count']:
            raise SubmissionError('Expanded bytes or record count mismatch')
    if not player_times or min(player_times)!=submission['time_start_game_seconds'] or max(player_times)!=submission['time_end_game_seconds']:
        raise SubmissionError('Player coverage disagrees with manifest')
    if observed_clans != {submission['metadata']['clan']}:
        raise SubmissionError('Clan differs from metadata')
    if submission.get('match_link')!=submission['metadata'].get('match_link'):
        raise SubmissionError('Submission match link differs from metadata')
    mode=resolve_mode(submission['metadata'],observed,submission.get('match_link'))
    if submission['mode_evidence']!=mode['mode_evidence']:
        raise SubmissionError('Mode evidence does not match native context/declaration')
    if submission.get('validation_stage')!=mode['validation_stage']:
        raise SubmissionError('Multiplayer requires the experimental unverified stage; singleplayer cannot claim it')
    for feedback in submission['metadata']['feedback']:
        if not min(player_times)<=feedback['game_time_seconds']<=max(player_times):
            raise SubmissionError('Feedback outside coverage')
    return submission


def validate_tree(root):
    root=Path(root)
    if not root.exists():
        return []
    if root.is_symlink():
        raise SubmissionError('Data root must not be a link')
    output=[];identities=set()
    for year in sorted(root.iterdir()):
        if year.name=='.gitkeep' and year.is_file() and not year.is_symlink() and year.stat().st_size==0:
            continue
        if year.name == 'README.md' and year.is_file() and not year.is_symlink():
            continue
        if year.is_symlink() or not year.is_dir() or not re.fullmatch(r'20[0-9]{2}',year.name):
            raise SubmissionError('Unexpected data year path')
        for match in sorted(year.iterdir()):
            if match.is_symlink() or not match.is_dir():
                raise SubmissionError('Match must be a directory')
            item=validate_submission(match)
            if match.name!=item['match_id'] or year.name!=item['created_date'][:4]:
                raise SubmissionError('Match path disagrees with submission ID/date')
            key=(item['capture_id'],item['source_session'])
            if key in identities:
                raise SubmissionError('Duplicate capture/session contribution')
            identities.add(key);output.append(item)
    team_groups(output)
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path',type=Path)
    parser.add_argument('--tree',action='store_true')
    args=parser.parse_args()
    try:
        result=validate_tree(args.path) if args.tree else [validate_submission(args.path)]
        print(json.dumps({'valid':True,'match_count':len(result),'match_ids':[x['match_id'] for x in result]}))
    except (SubmissionError,OSError,ValueError,zipfile.BadZipFile) as error:
        parser.exit(2,'Submission validation failed: '+str(error)+'\n')


if __name__=='__main__':
    main()
