"""Versioned, standard-library-only submission contract. No process or network APIs."""
from __future__ import annotations

import hashlib
import json
import math
import re

SCHEMA_VERSION = 1
MAX_RECORD = 2 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_TOTAL_EXPANDED = 1024 * 1024 * 1024
MAX_COMPRESSED = 20 * 1024 * 1024
DATA_FILES = ('player-observations.ndjson.gz', 'economic-snapshots.ndjson.gz',
              'actions.ndjson.gz', 'health.ndjson.gz')
FILES = ('submission.json', 'capture-manifest.json', 'checksums.sha256', 'SUMMARY.md', *DATA_FILES)


class SubmissionError(ValueError):
    pass


def loads(raw):
    def pairs(items):
        output = {}
        for key, value in items:
            if key in output:
                raise SubmissionError('Duplicate JSON key')
            output[key] = value
        return output
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(SubmissionError('Nonfinite JSON')))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise SubmissionError('Invalid JSON record') from error


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def obj(properties, required=()):
    return {'type': 'object', 'additionalProperties': False, 'properties': properties, 'required': list(required)}


def arr(items, maximum=10000):
    return {'type': 'array', 'items': items, 'maxItems': maximum}


def nullable(schema):
    return {'anyOf': [schema, {'type': 'null'}]}


NUM = {'type': 'number', 'minimum': -(2**53)+1, 'maximum': 2**53-1}
NONNEG = {'type': 'number', 'minimum': 0, 'maximum': 2**53-1}
INT = {'type': 'integer', 'minimum': 0, 'maximum': 2**53-1}
BOOL = {'type': 'boolean'}
TOKEN = {'type': 'string', 'pattern': r'^[A-Za-z][A-Za-z0-9_.:-]{0,127}$', 'maxLength': 128}
IDENTITY = {'type': 'string', 'pattern': r'^[a-f0-9]{24}$', 'maxLength': 24}
TEXT = {'type': 'string', 'maxLength': 1200}
SHA = {'type': 'string', 'pattern': r'^[a-f0-9]{64}$'}
VERSION = {'type': 'string', 'pattern': r'^[A-Za-z0-9_.-]{1,64}$'}
UUID = {'type': 'string', 'pattern': r'^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$'}
MATCH_LINK = obj({'shared_match_id':UUID,'declared_format':{'enum':['3v3','unknown']},
    'team_label':{'enum':['A','B','unknown']},'player_slot':nullable({'type':'integer','minimum':1,'maximum':3})},
    ('shared_match_id','declared_format','team_label','player_slot'))
DATE = {'type': 'string', 'pattern': r'^20[0-9]{2}-[01][0-9]-[0-3][0-9]$'}
POINT = obj({'x': NUM, 'y': NUM}, ('x','y'))
REF = obj({'uid': nullable(INT), 'entity_scope': TOKEN, 'kind': nullable(TOKEN), 'zone_id': nullable(INT)})
JOB = obj({'kind': TOKEN, 'args_status': TOKEN, 'args': arr(TOKEN, 0)}, ('kind',))
UNIT = obj({'uid':INT,**{k: nullable(INT) for k in ('zone_id','target_zone_id')}, 'kind': TOKEN,
            **{k: nullable(JOB) for k in ('assigned_job','cur_job')},
            **{k: nullable(REF) for k in ('assign_to','previous_assign_to','going_to')},
            **{k: BOOL for k in ('has_no_work','just_assigned','enabled','selected')},
            'produce': nullable(TOKEN), 'hit_life': NUM, 'position': nullable(POINT)}, ('uid','kind'))
BUILDING = obj({'uid': INT, 'zone_id': INT, 'kind': TOKEN,
                **{k: nullable(TOKEN) for k in ('base_kind','real_kind','target_kind')},
                **{k: BOOL for k in ('is_construction','wait_repair','marked_for_destroy','selected')},
                **{k: nullable(NUM) for k in ('upgraded','construction_progress','hit_life')},
                'assigned_unit_ids': arr(INT), 'position': nullable(POINT)}, ('uid','zone_id','kind'))
RESOURCE_NAMES = ('Food','Wood','Money','Stone','Iron','Lore')
STOCKS = obj({k: nullable(NUM) for k in RESOURCE_NAMES})
CACHE = obj({**{k: nullable(NUM) for k in ('stock','cached_at_game_time','age_game_seconds',
    'raw_cached_production_per_game_second','consumption_per_game_second','net_per_game_second',
    'gross_aggregate_per_game_second','total_produced_counter','stock_cap')},
    'stock_cap_verified': BOOL, 'stock_cap_source': TOKEN, 'stock_cap_status': TOKEN,
    'integration_order': nullable(TOKEN), 'native_net_field': nullable(TOKEN),
    'gross_aggregate_source': nullable(TOKEN), 'food_base_cache_is_total': nullable(BOOL)})
ECONOMY = obj({'schema_version': INT, 'status': TOKEN, 'scope': TOKEN, 'source': TOKEN,
    'hlboot_sha256': SHA, 'resource_table_fingerprint': SHA,
    **{k: NONNEG for k in ('game_time_seconds','time_before','time_after','sample_span_game_seconds')},
    **{k: IDENTITY for k in ('session_key','capture_run_id','capture_segment_id')},
    'atomic': BOOL, 'rate_unit': TOKEN, 'validation_status': TOKEN,
    'total_produced_semantics': TOKEN, 'read_guard_issues': arr(TOKEN,100),
    'reason_codes': arr(TOKEN,100), 'resources': obj({k:CACHE for k in ('Food','Wood','Money')})})
INTERVAL = obj({**{k: NONNEG for k in ('start_game_seconds','end_game_seconds','duration_game_seconds')},
    **{k: BOOL for k in ('start_exclusive','end_inclusive','paused_or_same_game_time')}})
CLOCK = obj({'year':INT,'month':{'type':'integer','minimum':1,'maximum':12},
             'phase':{'enum':['early','middle','late']},'month_second':NONNEG}, ('year','month','phase','month_second'))
OWN_EVENT = obj({'index':INT,'time':NONNEG,'kind':TOKEN,'actor':{'enum':['self']},'source':TOKEN,
    'zone_id':INT,'native_flag':BOOL,'building_kind':TOKEN,'tech':TOKEN,'unit_kind':TOKEN,
    'tech_id':TOKEN,'unit_or_upgrade_kind':TOKEN,
    'position':obj({'x':NUM,'y':NUM,'width':NUM,'height':NUM})}, ('kind','time'))
PAIR = lambda s: obj({'before':nullable(s),'after':nullable(s)})
CHANGES = obj({**{k:PAIR(NUM) for k in ('population','population_cap','construction_progress','upgraded','zone_id')},
    **{k:PAIR(TOKEN) for k in ('kind',)}, **{k:PAIR(BOOL) for k in ('in_winter','session_game_finished','has_no_work')},
    **{k:PAIR(REF) for k in ('assign_to','going_to')}, **{k:PAIR(JOB) for k in ('assigned_job','cur_job')},
    'target_zone_id':PAIR(INT), 'assigned_unit_ids':PAIR(arr(INT)),
    'discovered_zone_ids':obj({'added':INT}), 'owned_techs':obj({'added':TOKEN})})
ACTION_EVENT = obj({'kind':TOKEN,'event_id':TOKEN,'time_interval':INTERVAL,
    'entity':obj({**UNIT['properties'],**BUILDING['properties']}), 'changes':CHANGES,
    'source':TOKEN,'confidence':obj({'level':TOKEN}),'resulting_building':nullable(BUILDING)}, ('kind',))
BASE = {'status':TOKEN,'sequence':INT,'game_time_seconds':nullable(NONNEG),'game_clock':CLOCK,
    **{k: IDENTITY for k in ('session_key','capture_run_id','capture_segment_id')},
    'sample_span_game_seconds':nullable(NONNEG),'clan':TOKEN}
RECORD_SCHEMAS = {
 'player-observations.ndjson.gz':obj({**BASE,'schema_version':INT,'observation_type':TOKEN,'policy_version':TOKEN,
    'scope':{'enum':['local_player_and_owned_zones']},'population':nullable(NONNEG),'population_cap':nullable(NONNEG),
    'stocks':STOCKS,'in_winter':BOOL,'units':arr(UNIT),'buildings':arr(BUILDING),
    'owned_zone_ids':arr(INT),'discovered_zone_ids':arr(INT),'economy':ECONOMY,'own_events':arr(OWN_EVENT,100000),
    **{k:BOOL for k in ('session_game_finished','local_player_game_over','local_player_has_conceded')}}),
 'economic-snapshots.ndjson.gz':obj({**BASE,'population':nullable(NONNEG),'stocks':STOCKS,'in_winter':BOOL,
    'economy':ECONOMY,'own_success_events':arr(OWN_EVENT,100000),
    'action_events':arr(obj({'kind':TOKEN,'time_interval':INTERVAL}),100000)}),
 'actions.ndjson.gz':obj({**BASE,'record_kind':TOKEN,'reason':TOKEN,'segment':INT,'sample_index':INT,
    'time_interval':INTERVAL,'events':arr(ACTION_EVENT,100000),'attribution':TOKEN,
    'resource_changes':obj({k:obj({'before':NUM,'after':NUM,'delta':NUM,'net_observed_change':NUM,
        'observed_net_rate_per_game_second':nullable(NUM)}) for k in RESOURCE_NAMES}),
    'native_events_already_present':INT,'unit_count':INT,'building_count':INT,
    'skipped_samples_since_previous_valid':INT}),
 'health.ndjson.gz':obj({**BASE,**{k:nullable(TOKEN) for k in ('economy_status','operations_status','map_status','feast_quote_status',
        'labor_status','individual_rate_status','population_growth_status')},
    'labor_unknown_fields':arr(TOKEN,256),'growth_unknown_fields':arr(TOKEN,256),
    'food_capacity_verified':BOOL,'reason_code':TOKEN,'error_type':TOKEN,'observation_rejection_codes':arr(TOKEN,100),
    'component_diagnostics':obj({name:obj({'source_status':TOKEN,'projection_status':TOKEN,'error_type':TOKEN,
        'source_reason_codes':arr(TOKEN,100),'projection_reason_codes':arr(TOKEN,100),'read_guard_issues':arr(TOKEN,100)},
        ('source_status','projection_status')) for name in
        ('economy','operations','map_observation','labor','population_growth','match_context')})}),
}

# New 0.3 inputs remain separate from actual rates. This frozen field tree is
# independently checked in CI, without importing any candidate collector code.
NULL = {'type':'null'}
COMMON_COMPONENT = {'schema_version':{'const':1},'status':TOKEN,'source':TOKEN,'scope':TOKEN,
    'hlboot_sha256':SHA,'data_cdb_sha256':SHA,'local_player_entity_uid':INT,
    **{k:NONNEG for k in ('game_time_seconds','time_before','time_after','sample_span_game_seconds')},
    **{k:IDENTITY for k in ('session_key','capture_run_id','capture_segment_id')},
    'atomic':{'const':False},'unknown_fields':arr(TOKEN,256),'reason_codes':arr(TOKEN,256)}
EFFECTS = nullable(arr(obj({'kind':TOKEN,'raw_end_time':NUM},('kind','raw_end_time')),1024))
ENTRIES = nullable(arr(obj({'id':TOKEN,'value':{'anyOf':[NUM,BOOL,NULL]}},('id','value')),1024))
LABOR_INPUTS = obj({**{k:nullable(BOOL) for k in ('has_no_work','just_assigned','enabled')},
    **{k:nullable(NUM) for k in ('hit_life','sick_counter','status_timer','flags')},
    **{k:nullable(TOKEN) for k in ('produce','sick_reason','status','cur_job_kind','assigned_job')}})
LABOR_UNIT = obj({'uid':INT,'kind':TOKEN,'zone_id':nullable(INT),'inputs':LABOR_INPUTS,'effects':EFFECTS,
    'native_actual_rate':NULL,'native_settled_amount':NULL,'model_effective_rate':NULL,'can_produce':NULL,
    'rate_status':{'const':'computed_getter_no_persistent_unit_cache'},
    'can_produce_status':{'const':'getter_not_executed'},
    'assignment':nullable(obj({'building_uid':INT,'zone_id':INT,'same_zone':BOOL},('building_uid','zone_id','same_zone'))),
    'assignment_status':TOKEN,'configuration_candidates':nullable(arr(obj({'resource':TOKEN,
        'configured_base_q':nullable(NUM),'converts_to':nullable(TOKEN),'building_kinds':arr(TOKEN,64),
        'additional_requirements_present':BOOL,'requirement_status':{'const':'not_evaluated'},
        'rate_semantics':{'const':'static_configuration_candidate_not_effective_rate'}},
        ('resource','configured_base_q','converts_to','building_kinds','additional_requirements_present','requirement_status','rate_semantics')),64))},
    ('uid','kind','native_actual_rate','native_settled_amount','model_effective_rate','can_produce','rate_status'))
LABOR = obj({**COMMON_COMPONENT,'native_individual_rates_available':{'const':False},
    'actual_settled_amounts_available':{'const':False},
    'individual_rate_status':{'const':'computed_getter_no_persistent_unit_cache'},
    'player_context':obj({'clan':nullable(TOKEN),
        **{k:nullable(BOOL) for k in ('in_winter','overworking','special_tech_provider_present','owl_component_present')},
        **{k:nullable(NUM) for k in ('happiness_cache','consumption_coefficient','last_feast_time','last_overwork_time')},
        **{k:nullable(INT) for k in ('population_cache','fame_level')},
        'researched_tech_entries':ENTRIES,'upgraded_unit_entries':ENTRIES,'effects':EFFECTS,
        'modifier_coverage':{'const':'materialized_inputs_only_not_complete_effective_bonus'}}),
    'units':arr(LABOR_UNIT),'zones':arr(obj({'zone_id':INT,'effects':EFFECTS,'flags':nullable(NUM)}),4096),
    'buildings':arr(obj({'uid':INT,'kind':TOKEN,'zone_id':INT,'upgraded':nullable(INT),
        **{k:nullable(NUM) for k in ('hit_life','state_progress','state_contains')},
        **{k:nullable(BOOL) for k in ('extended','wait_repair','harbor_force_stop')},
        'harbor_produce_choice':nullable(TOKEN),'effects':EFFECTS}))})
GROWTH_NATIVE = obj({**{k:nullable(NONNEG) for k in ('resource_growth_progress','recruit_stack','low_housing_timer',
    'low_happiness_timer','no_population_growth_time')},'current_happiness':nullable(NUM),
    **{k:nullable(INT) for k in ('population_cache','population_cap_cache','reserve_population_cache')},
    **{k:nullable(BOOL) for k in ('force_recruit','has_conceded','player_game_over','game_finished',
        'debug_population_can_grow','growth_disabled_modifier')}})
PROGRESS_INTERVAL = obj({'town_hall_uid':INT,'source':{'const':'observed_growth_progress_interval'},
    **{k:NONNEG for k in ('start_game_seconds','end_game_seconds','elapsed_game_seconds','progress_before','progress_after','observed_progress_per_game_second')},
    'observed_effective_bonus_estimate':nullable(NUM),'bonus_estimate_semantics':TOKEN,
    'conditional_threshold_eta_game_seconds':nullable(NONNEG),'eta_semantics':TOKEN,
    'inputs_observed_unchanged_at_endpoints':{'const':True},'within_interval_changes_excluded':{'const':False},
    'unknown_fields':arr(TOKEN,100),**{k:IDENTITY for k in ('session_key','capture_run_id','capture_segment_id')}})
PROGRESS_INTERVAL['required']=list(PROGRESS_INTERVAL['properties'])
GROWTH = obj({**COMMON_COMPONENT,'policy_version':TOKEN,'clan':TOKEN,'native_inputs':GROWTH_NATIVE,
    'town_halls':arr(obj({'uid':INT,'zone_id':INT,'growth_progress':nullable(NONNEG),'force_recruit':nullable(BOOL),
        'recruit_stack':nullable(NONNEG),'enabled':nullable(BOOL),'progress_fraction_model':nullable(NONNEG)}),1000),
    'bonus_inputs':obj({'normal_map_tech_presence':nullable(obj({k:BOOL for k in ('Recruit','EndlessTide','PlotThrone')})),
        'special_tech_providers_present':nullable(BOOL),
        **{k:nullable(INT) for k in ('active_meal_count','conquest_bonus_count','additional_rule_count')}}),
    'growth_bonus':NULL,'growth_bonus_source':{'const':'not_a_stored_field_not_evaluated'},
    'model':obj({'next_recruit_threshold':nullable(NONNEG),'threshold_source':nullable(TOKEN),
        'population_input':nullable(NONNEG),'population_input_source':TOKEN,'population_cache_matches_parent':nullable(BOOL),
        'observed_blocking_inputs':arr(TOKEN,100),'blocking_inputs_complete':{'const':False},
        'formula_eta_game_seconds':NULL,'formula_eta_status':TOKEN}),
    'observed_progress_intervals':arr(PROGRESS_INTERVAL,1000)})
OBSERVED_WINDOW = obj({'previous_game_seconds':NONNEG,'observed_by_game_seconds':NONNEG,
    'sample_interval_game_seconds':NONNEG,'lower_exclusive':BOOL})
BRIEF = obj({'kind':TOKEN,'zone_id':nullable(INT),'assign_to':nullable(REF),'going_to':nullable(REF),
    'assigned_job':nullable(TOKEN),'cur_job':nullable(TOKEN),'target_zone_id':nullable(INT),'produce':nullable(TOKEN),
    'enabled':nullable(BOOL),'has_no_work':nullable(BOOL),'position':nullable(POINT)})
TRANSITION = obj({'schema_version':{'const':1},'event_type':{'const':'labor_transition_milestone'},
    'source':{'const':'own_unit_sampled_state_transition'},'transition_id':TOKEN,'unit_uid':INT,
    'phase':{'enum':['assignment_changed_observed','assignment_cleared_observed','task_or_route_changed_observed',
        'location_changed_observed','occupation_changed_observed','production_candidate_resumed_observed','tracking_incomplete']},
    'reason_code':nullable(TOKEN),'unit_class_before':TOKEN,'unit_class_now':TOKEN,
    'assignment_observed_window':OBSERVED_WINDOW,'milestone_observed_window':OBSERVED_WINDOW,
    'elapsed_from_assignment_game_seconds_bounds':nullable(obj({'lower':NONNEG,'upper':NONNEG},('lower','upper'))),
    'assignment_sample_index':INT,'milestone_sample_index':INT,'before':BRIEF,'now':BRIEF,
    'actual_resource_production_confirmed':{'const':False},
    'duration_semantics':{'const':'sample_bounded_assignment_to_observed_stage_not_pure_walking_time'},
    'unknown_fields':arr(TOKEN,100),**{k:IDENTITY for k in ('session_key','capture_run_id','capture_segment_id')}})
TRANSITION['required']=list(TRANSITION['properties'])

# Previously verified colonization/building costs and discovered static cells
# remain shareable; hidden dynamic entities are never part of these schemas.
COST = arr(obj({'resource':TOKEN,'amount':NONNEG},('resource','amount')),64)
FEAST = obj({**{k:nullable(BOOL) for k in ('instigator_is_self','team_leader_present','team_leader_is_self',
    'has_relic_goat','has_relic_chalice','has_trial_faith')},
    **{k:nullable(INT) for k in ('feast_count','free_feast','free_feast_count','living_legend_stacks')},
    **{k:nullable(NUM) for k in ('last_feast','free_feast_time','goat_cost_reduction','hunt_cost_reduction','living_legend_cost_reduction')}})
OPERATIONS = obj({**COMMON_COMPONENT,'complete_transaction_ledger':{'const':False},'feast':FEAST,
    'colonizations':arr(obj({'zone_id':INT,'progress':nullable(NUM),'cost_reduction':nullable(NUM),
        'started_by_self':nullable(BOOL),'paid_cost':nullable(COST),'extra_paid_cost':nullable(COST),
        'paid_cost_record_status':TOKEN,'extra_paid_cost_record_status':TOKEN}),4096),
    'building_costs':arr(obj({'uid':INT,'zone_id':INT,'kind':TOKEN,'was_free':nullable(BOOL),'cost':nullable(COST),'cost_record_status':TOKEN})),
    'feast_quote':obj({'status':TOKEN,'source':TOKEN,'cost':COST,'formula_branch':TOKEN})})
MAP = obj({**COMMON_COMPONENT,'policy_version':TOKEN,'visibility_policy':TOKEN,'adjacency_semantics':TOKEN,
    'map_identity':obj({'map_instance_id':IDENTITY,'biome':TOKEN,
        'seed':nullable(NUM),'seed_source':nullable(TOKEN),'generation_seed':nullable(NUM),'generation_seed_source':nullable(TOKEN)}),
    'known_zones':arr(obj({'id':INT,'terrain_kind':{'enum':['Default','Water','Coast']},'position':POINT,
        'floor':NUM,'has_lake':BOOL,'known_geometric_neighbor_ids':arr(INT)}))})
# Derived spatial annotations retain only the current discovered static graph
# and references to entities already admitted by the own-player contract.
BASE_RING_REASONS = [
    'verified_player_observation_unavailable','known_graph_only_complete_rings_unverified',
    'own_entity_references_unavailable','unverified_entity_reference_omitted',
    'duplicate_own_entity_uid_omitted','own_completed_town_hall_ambiguous',
    'own_completed_town_hall_unavailable','base_anchor_unverified_or_outside_current_clock',
    'known_map_unavailable_or_unverified','unverified_map_zone_omitted','duplicate_map_zone_omitted',
    'known_map_rows_incomplete','invalid_geometric_edges_omitted',
    'duplicate_geometric_edges_deduplicated','asymmetric_geometric_edges_omitted',
    'anchor_town_hall_not_currently_observed','anchor_zone_not_in_current_known_map']
BASE_RING_UNKNOWNS = ['complete_base_neighborhood','current_visibility','traversability',
                      'non_owned_zone_owner','original_spawn_zone']
ZONE_ID = {'type':'integer','minimum':0,'maximum':2**31-1}
ENTITY_UID = {'type':'integer','minimum':1,'maximum':2**53-1}
BASE_RING_ANCHOR = obj({'selection':{'const':'first_observed_own_town_hall'},
    'policy':{'const':'fixed_within_session'},'original_spawn_status':{'const':'unverified'},
    'zone_id':nullable(ZONE_ID),'town_hall_uid':nullable(ENTITY_UID),
    'town_hall_reference_status':{'enum':['observed_own_completed','not_currently_observed']},
    'first_observed_game_time_seconds':NONNEG})
BASE_RING_ANCHOR['required']=list(BASE_RING_ANCHOR['properties'])
BASE_RING_ZONE = obj({'zone_id':ZONE_ID,'exploration':{'const':'known'},
    'current_visibility':{'const':'unverified'},'ownership':{'enum':['self','unknown']},
    'observed_graph_distance':nullable({'type':'integer','minimum':0,'maximum':2}),
    'witness_path_zone_ids':arr(ZONE_ID,3),'own_unit_uids':arr(ENTITY_UID),
    'own_building_uids':arr(ENTITY_UID),'map_observed_game_time_seconds':nullable(NONNEG),
    'own_entities_observed_game_time_seconds':NONNEG})
BASE_RING_ZONE['required']=list(BASE_RING_ZONE['properties'])
BASE_NEIGHBORHOOD = obj({'schema_version':{'const':1},'policy_version':{'const':'base-neighborhood.v1'},
    'status':{'enum':['unknown','partial']},'source':{'const':'derived_known_geometric_map_adjacency'},
    'scope':{'const':'local_player_known_base_neighborhood'},
    'adjacency_semantics':{'const':'geometric_map_cell_neighbors_not_traversability'},
    'distance_semantics':{'const':'shortest_path_in_current_known_reciprocal_graph'},
    'coverage':{'const':'incomplete'},
    'coverage_semantics':{'const':'empty_rings_mean_not_observed_not_absent'},
    'anchor':nullable(BASE_RING_ANCHOR),
    **{key:arr(ZONE_ID) for key in ('ring0_zone_ids','ring1_zone_ids','ring2_zone_ids','known_unclassified_zone_ids')},
    'zones':arr(BASE_RING_ZONE),'reason_codes':arr({'enum':BASE_RING_REASONS},len(BASE_RING_REASONS)),
    'unknown_fields':arr({'enum':BASE_RING_UNKNOWNS},len(BASE_RING_UNKNOWNS)),
    'continuity':{'enum':['current_sample_only','initial_baseline','baseline_after_read_error',
        'capture_boundary_current_sample_only','clock_rollback_current_sample_only',
        'sample_gap_current_sample_only','paused_clock_current_sample_only']}})
BASE_NEIGHBORHOOD['required']=list(BASE_NEIGHBORHOOD['properties'])
BASE_NEIGHBORHOOD['properties'].update({**{key:IDENTITY for key in ('session_key','capture_run_id','capture_segment_id')},
    'local_player_entity_uid':ENTITY_UID,'game_time_seconds':NONNEG,'map_observed_game_time_seconds':nullable(NONNEG)})
CAP_COMPONENTS = obj({'base':NONNEG,'silos':arr(obj({'building_uid':INT,'zone_id':INT,'is_upgraded':BOOL,'capacity_added':NONNEG}))})
CACHE['properties']['stock_cap_components']=CAP_COMPONENTS
ECONOMY['properties']['resource_limits']=obj({k:obj({'stock_cap':nullable(NUM),'stock_cap_verified':BOOL,
    'stock_cap_source':TOKEN,'stock_cap_status':TOKEN}) for k in RESOURCE_NAMES})
RECORD_SCHEMAS['player-observations.ndjson.gz']['properties'].update({
    'local_player_entity_uid':INT,'labor':LABOR,'population_growth':GROWTH,'operations':OPERATIONS,'map_observation':MAP,
    'base_neighborhood':BASE_NEIGHBORHOOD})
RECORD_SCHEMAS['player-observations.ndjson.gz']['properties']['game_context']=obj({
    **{k:TOKEN for k in ('mode_kind','game_type_kind','mode_variant_kind','biome')},
    'bifrost_data_present':BOOL,'editor_present':BOOL,'difficulty':NUM})
TEAM_LAYOUT = {'type':'string','pattern':r'^[1-9][0-9]?(?:v[1-9][0-9]?){0,63}$','maxLength':191}
PLAY_MODES = ['Single','Multiplayer','Campaign','Conquest','Singleplayer','Bifrost']
MULTI_MODES = ['Duel','FreeForAll','Team2V2','Team3V3','Team2V2V2','Team4V4','Team2V2V2V2','FreeTeams']
CONFIGURED_LAYOUTS = ['1v1','2v2','3v3','2v2v2','4v4','2v2v2v2']
MATCH_CONTEXT = obj({**COMMON_COMPONENT,'source':{'const':'native_match_context_read_only'},
    'scope':{'enum':['match_roster_counts_and_local_team','no_match_context_data']},
    'status':{'enum':['ok','partial','rejected']},
    'native_mode_kind':nullable({'enum':PLAY_MODES}),
    'native_mode_variant':nullable({'enum':MULTI_MODES}),
    'game_type_kind':nullable({'enum':['Standard','Private','Ranked']}),
    **{key:nullable({'type':'integer','minimum':1,'maximum':64}) for key in
        ('native_player_count','native_current_player_count','native_team_count','native_own_team_size')},
    'native_local_team_id':nullable({'type':'integer','minimum':0,'maximum':63}),
    'native_other_team_sizes':nullable(arr({'type':'integer','minimum':1,'maximum':64},63)),
    'team_layout':nullable(TEAM_LAYOUT),'team_layout_status':{'enum':['verified_native_roster','unavailable']},
    'configured_team_layout':nullable({'enum':CONFIGURED_LAYOUTS}),
    'configured_layout_matches_roster':nullable(BOOL),
    'roster_source':{'const':'GameState.startPlayers'},'team_basis':{'const':'Player.teamLeader_or_self'},
    'team_id_semantics':{'const':'zero_based_start_roster_group_order'},
    'player_count_semantics':{'const':'start_roster_slots_including_ai_if_present'}},
    ('schema_version','source','scope','status'))
MATCH_CONTEXT['properties']['native_current_player_count']={'type':'integer','minimum':1,'maximum':64}
MATCH_CONTEXT['properties']['hlboot_sha256']={'const':'db0459d251879698420f58f6819e0ab2f75a3cde6e65611f2ad723f3b4b7ef76'}
MATCH_CONTEXT['properties']['data_cdb_sha256']={'const':'88d4c84b9622ce237533afc8674174aa9006d87a03258cd7e5fd671f930174e3'}
RECORD_SCHEMAS['player-observations.ndjson.gz']['properties']['match_context']=MATCH_CONTEXT
RECORD_SCHEMAS['health.ndjson.gz']['properties'].update({
    'match_context_status':nullable(TOKEN),'base_neighborhood_status':nullable({'enum':['unknown','partial']}),
    'native_mode_kind':nullable(TOKEN),'native_mode_variant':nullable(TOKEN),
    'capture_validation_stage':{'enum':['experimental_multiplayer_unverified','experimental_singleplayer_unverified','native_mode_unverified']},
    'contributor_link_status':TOKEN})
RECORD_SCHEMAS['health.ndjson.gz']['properties']['component_diagnostics']['properties']['base_neighborhood']=obj({
    'source_status':TOKEN,'projection_status':TOKEN,'error_type':TOKEN,
    'source_reason_codes':arr(TOKEN,100),'projection_reason_codes':arr(TOKEN,100),'read_guard_issues':arr(TOKEN,100)},
    ('source_status','projection_status'))
RECORD_SCHEMAS['actions.ndjson.gz']['properties']['labor_transitions']=arr(TRANSITION,10000)

METADATA = obj({'consent_to_share':{'const':True}, 'data_source':{'enum':['own_human_game']},
    'mode':{'enum':['singleplayer','multiplayer','unknown']},'clan':nullable(TOKEN),'match_link':nullable(MATCH_LINK),
    'difficulty':nullable(TOKEN), 'map':obj({'name':nullable(TOKEN),'seed':nullable({'type':'integer'}),'size':nullable(TOKEN)},('name','seed','size')),
    'experience':{'enum':['beginner','intermediate','experienced','expert','unknown']},
    'goal':TEXT,'result':{'enum':['victory','defeat','quit','incomplete','unknown']},
    'tags':arr({'type':'string','pattern':r'^[A-Za-z0-9_\-\u4e00-\u9fff]{1,40}$'},20),
    'feedback':arr(obj({'game_time_seconds':NONNEG,'observation':TEXT,'hypothesis':TEXT,'suggestion':TEXT,'counterexample':TEXT,
                       'time_precision':{'enum':['exact_game_clock','sampled_interval','month_phase','approximate']},
                       'confidence':{'enum':['observed','suspected','unverified']},'unit_uids':arr(INT,100)},
                      ('game_time_seconds','time_precision','observation','hypothesis','suggestion','counterexample','confidence')),100)},
    ('consent_to_share','data_source','mode','clan','difficulty','map','experience','goal','result','tags','feedback'))
FILE_ENTRY = obj({'path':{'enum':list(DATA_FILES)+['capture-manifest.json','SUMMARY.md']},'size_bytes':INT,'sha256':SHA,
                  'expanded_bytes':INT,'record_count':INT},('path','size_bytes','sha256','expanded_bytes','record_count'))
SUBMISSION_SCHEMA = obj({'schema_version':{'const':1},'artifact_type':{'const':'northgard_own_player_submission'},
    'match_id':UUID,'created_date':DATE,'metadata':METADATA,
    'collector_version':VERSION,'game_build':nullable(VERSION),
    'game_fingerprint':obj({'hlboot_sha256':nullable(SHA),'data_cdb_sha256':nullable(SHA)},('hlboot_sha256','data_cdb_sha256')),
    'mode_evidence':{'enum':['native_singleplayer','native_multiplayer','contributor_declared_native_context_unavailable','unknown']},
    'match_link':nullable(MATCH_LINK),'validation_stage':{'const':'experimental_multiplayer_unverified'},
    'capture_id':IDENTITY,'source_session':IDENTITY,'time_start_game_seconds':NONNEG,'time_end_game_seconds':NONNEG,
    'clock_policy':{'const':'A800_March_epoch_60_game_seconds_per_month_thirds_are_report_buckets'},
    'scope':{'const':'own_player_core_whitelist_v1_no_opponent_or_raw_memory'},
    'files':arr(FILE_ENTRY,6),'record_schema_version':{'const':1}},
    ('schema_version','artifact_type','match_id','created_date','metadata','collector_version','game_build','game_fingerprint','mode_evidence',
     'capture_id','source_session','time_start_game_seconds','time_end_game_seconds','clock_policy','scope','files','record_schema_version'))
MANIFEST_SCHEMA = obj({'schema_version':{'const':1},'collector_version':VERSION,'recording_state':{'const':'stopped'},
    'source_collection_id':IDENTITY,'projection':{'const':'submission-core.v1'},'dropped_source_fields':INT,
    'excluded_files_policy':{'const':'fixed_names_only_no_raw_no_latest_no_paths_no_opponents'}},
    ('schema_version','collector_version','recording_state','source_collection_id','projection','dropped_source_fields','excluded_files_policy'))


def validate(value, schema, path='$', depth=0):
    if depth > 48:
        raise SubmissionError('Too deeply nested at ' + path)
    if 'anyOf' in schema:
        for choice in schema['anyOf']:
            try:
                validate(value, choice, path, depth+1)
                return
            except SubmissionError:
                pass
        raise SubmissionError('Unexpected nullable value at ' + path)
    if 'const' in schema and (type(value) is not type(schema['const']) or value != schema['const']):
        raise SubmissionError('Unexpected constant at ' + path)
    if 'enum' in schema and value not in schema['enum']:
        raise SubmissionError('Unexpected enum at ' + path)
    kind = schema.get('type')
    checks = {'object':lambda:type(value) is dict, 'array':lambda:type(value) is list,
              'number':lambda:(type(value) is int and abs(value)<2**53) or (type(value) is float and math.isfinite(value)),
              'integer':lambda:type(value) is int,'string':lambda:type(value) is str,
              'boolean':lambda:type(value) is bool,'null':lambda:value is None}
    if kind and not checks[kind]():
        raise SubmissionError('Unexpected type at ' + path)
    if kind == 'object':
        if set(value) - set(schema['properties']):
            raise SubmissionError('Unknown field at ' + path)
        if set(schema.get('required',())) - set(value):
            raise SubmissionError('Missing field at ' + path)
        for key,item in value.items():
            validate(item,schema['properties'][key],path+'.'+key,depth+1)
    elif kind == 'array':
        if len(value) > schema.get('maxItems',10000):
            raise SubmissionError('Too many items at ' + path)
        for index,item in enumerate(value):
            validate(item,schema['items'],path+'['+str(index)+']',depth+1)
    elif kind in ('number','integer'):
        if value < schema.get('minimum',-float('inf')) or value > schema.get('maximum',float('inf')):
            raise SubmissionError('Number out of range at ' + path)
    elif kind == 'string':
        if len(value) > schema.get('maxLength',10000) or ('pattern' in schema and not re.fullmatch(schema['pattern'],value)):
            raise SubmissionError('Invalid string at ' + path)
        # Free text remains manually reviewable; block common accidental private artifacts.
        if re.search(r'(?i)([A-Z]:[\\/]|\\\\|(?:^|\s)/(?:home|Users)/|gh[pousr]_[A-Za-z0-9]|github_pat_|sk-[A-Za-z0-9]{12}|STEAM_[0-9]|\b7656119[0-9]{10}\b|-----BEGIN .*PRIVATE KEY|https?://[^\s]+@)', value):
            raise SubmissionError('Possible local path/account/credential in ' + path)


def project(value, schema, dropped=None):
    """Only named fields survive; type-check every retained leaf afterwards."""
    if dropped is None:
        dropped = [0]
    if 'anyOf' in schema:
        return None if value is None else project(value,schema['anyOf'][0],dropped)
    if schema.get('type') == 'object' and type(value) is dict:
        dropped[0] += len(set(value)-set(schema['properties']))
        return {k:project(v,schema['properties'][k],dropped) for k,v in value.items() if k in schema['properties']}
    if schema.get('type') == 'array' and type(value) is list:
        return [project(v,schema['items'],dropped) for v in value]
    return value


def clock(seconds):
    months = math.floor(seconds/60)+2
    second = seconds % 60
    return {'year':800+months//12,'month':months%12+1,
            'phase':'early' if second < 20 else 'middle' if second < 40 else 'late','month_second':second}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def mode_facts():
    return {'modes':set(),'variants':set(),'roster_layouts':set(),'count_layouts':set(),'configured_layouts':set()}


VARIANT_LAYOUTS = {'Duel':'1v1','Team2V2':'2v2','Team3V3':'3v3','Team2V2V2':'2v2v2',
                  'Team4V4':'4v4','Team2V2V2V2':'2v2v2v2'}


def observe_mode(facts,row):
    """Use only source-bound player context, never health or contributor tags."""
    context=row.get('game_context',{})
    if context.get('mode_kind') is not None:
        facts['modes'].add(context['mode_kind'])
    if context.get('mode_variant_kind') is not None:
        facts['variants'].add(context['mode_variant_kind'])
    context=row.get('match_context',{})
    if context.get('status') not in ('ok','partial'):
        return
    if context.get('native_mode_kind') is not None:
        facts['modes'].add(context['native_mode_kind'])
    if context.get('native_mode_variant') is not None:
        facts['variants'].add(context['native_mode_variant'])
    if context.get('configured_team_layout') is not None:
        facts['configured_layouts'].add(context['configured_team_layout'])
    if context.get('team_layout_status')=='verified_native_roster' and context.get('team_layout') is not None:
        facts['roster_layouts'].add(context['team_layout'])
    own,others=context.get('native_own_team_size'),context.get('native_other_team_sizes')
    if type(own) is int and type(others) is list and all(type(size) is int for size in others):
        facts['count_layouts'].add('v'.join(str(size) for size in sorted([own,*others],reverse=True)))


def resolve_mode(metadata,facts,link=None):
    native_modes={'Singleplayer' if mode=='Single' else mode for mode in facts['modes']}
    if len(native_modes)>1 or native_modes-set(('Singleplayer','Multiplayer')):
        raise SubmissionError('Conflicting or unsupported native play modes')
    if len(facts['variants'])>1 or len(facts['roster_layouts'])>1 or len(facts['count_layouts'])>1 or len(facts['configured_layouts'])>1:
        raise SubmissionError('Native match mode/layout sources disagree')
    configured=set(facts['configured_layouts'])
    configured.update(VARIANT_LAYOUTS[value] for value in facts['variants'] if value in VARIANT_LAYOUTS)
    if len(configured)>1:
        raise SubmissionError('Configured native layout disagrees with native mode variant')
    declared=metadata['mode']
    native=next(iter(native_modes),None)
    if ((native=='Singleplayer' and declared=='multiplayer') or
            (native=='Multiplayer' and declared=='singleplayer')):
        raise SubmissionError('Contributor mode conflicts with native mode')
    if link is not None and (native=='Singleplayer' or declared=='singleplayer'):
        raise SubmissionError('A singleplayer match cannot claim a shared multiplayer match link')
    if link is not None and link['declared_format']=='3v3':
        if (configured and configured!={'3v3'}) or (facts['count_layouts'] and facts['count_layouts']!={'3v3'}):
            raise SubmissionError('Declared 3v3 format conflicts with known native layout')
    uncertain_roster=(native is None and declared=='unknown' and bool(
        facts['count_layouts'] or facts['variants'] or facts['configured_layouts']))
    multiplayer=(native=='Multiplayer' or declared=='multiplayer' or link is not None or uncertain_roster)
    evidence=('native_multiplayer' if native=='Multiplayer' else 'native_singleplayer' if native=='Singleplayer' else
              'contributor_declared_native_context_unavailable' if declared in ('singleplayer','multiplayer') else 'unknown')
    return {'mode_evidence':evidence,
            'validation_stage':'experimental_multiplayer_unverified' if multiplayer else None}


def team_groups(submissions):
    """Candidate associations only; local IDs and team ordinals never join peers."""
    grouped={}
    for item in submissions:
        link=item.get('match_link')
        if link is None:
            continue
        shared_id=link['shared_match_id']
        fingerprint=item['game_fingerprint']
        signature=(item['collector_version'],item['game_build'],fingerprint['hlboot_sha256'],
                   fingerprint['data_cdb_sha256'],link['declared_format'])
        group=grouped.get(shared_id)
        if group is None:
            group={'shared_match_id':shared_id,'association_status':'contributor_declared_candidate_unverified',
                'declared_format':link['declared_format'],'collector_version':item['collector_version'],
                'game_build':item['game_build'],'game_fingerprint':fingerprint,
                'cross_client_identity_policy':'no_session_unit_or_native_team_id_join',
                'members':[],'declared_slots':{'A':[],'B':[]},'_signature':signature,'_positions':set()}
            grouped[shared_id]=group
        elif signature!=group['_signature']:
            raise SubmissionError('Shared match ID has incompatible collector/game versions, fingerprints or format')
        team,slot=link['team_label'],link['player_slot']
        if team in ('A','B') and slot is not None:
            position=(team,slot)
            if position in group['_positions']:
                raise SubmissionError('Shared match ID contains a duplicate declared team/player slot')
            group['_positions'].add(position);group['declared_slots'][team].append(slot)
        group['members'].append({'match_id':item['match_id'],'team_label':team,'player_slot':slot,
            'clan':item['metadata']['clan'],'mode_evidence':item['mode_evidence'],
            'validation_stage':item.get('validation_stage')})
    output=[]
    for key in sorted(grouped):
        group=grouped[key]
        group.pop('_signature');group.pop('_positions')
        group['members'].sort(key=lambda item:item['match_id'])
        for slots in group['declared_slots'].values():slots.sort()
        output.append(group)
    return output
