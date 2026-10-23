"""Freeze a small roster-count contract against the verified own-player frame.

No roster identities are exported. Counts describe starting player slots,
including AI if present; neither native team ordinal nor session is a global
cross-computer match identifier. A configured Team3V3 alone does not prove six
observed players: the team layout also requires a complete native roster.
"""
from __future__ import annotations

import math
import re

SOURCE = 'native_match_context_read_only'
SCOPE = 'match_roster_counts_and_local_team'
HLBOOT_SHA256 = 'db0459d251879698420f58f6819e0ab2f75a3cde6e65611f2ad723f3b4b7ef76'
DATA_CDB_SHA256 = '88d4c84b9622ce237533afc8674174aa9006d87a03258cd7e5fd671f930174e3'
IDENTITIES = ('session_key', 'capture_run_id', 'capture_segment_id')
IDENTITY = re.compile(r'[A-Za-z0-9_:-]{1,128}\Z')
PLAY_MODES = frozenset(('Single', 'Multiplayer', 'Campaign', 'Conquest', 'Singleplayer', 'Bifrost'))
MULTI_MODES = frozenset(('Duel', 'FreeForAll', 'Team2V2', 'Team3V3', 'Team2V2V2', 'Team4V4', 'Team2V2V2V2', 'FreeTeams'))
GAME_TYPES = frozenset(('Standard', 'Private', 'Ranked'))
CONFIGURED_LAYOUTS = {'Duel': '1v1', 'Team2V2': '2v2', 'Team3V3': '3v3',
    'Team2V2V2': '2v2v2', 'Team4V4': '4v4', 'Team2V2V2V2': '2v2v2v2'}
COUNT_FIELDS = ('native_player_count', 'native_team_count', 'native_own_team_size',
                'native_other_team_sizes', 'native_local_team_id')
REASONS = frozenset(('match_context_mode_unavailable', 'match_context_game_type_unavailable',
    'match_context_roster_or_team_unavailable', 'match_context_configured_layout_mismatch'))
UNKNOWNS = frozenset(('native_mode_kind', 'game_type_kind', 'roster_counts', 'team_layout'))


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value < 2**53


def _count(value):
    return type(value) is int and 1 <= value <= 64


def _reject(reason):
    return {'schema_version': 1, 'status': 'rejected', 'source': SOURCE,
        'scope': 'no_match_context_data', 'atomic': False,
        'reason_codes': [reason], 'unknown_fields': ['all']}


def project_match_context(value, snapshot):
    if (type(value) is not dict or type(snapshot) is not dict
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value.get('source') != SOURCE or value.get('scope') != SCOPE
            or value.get('status') not in ('ok', 'partial') or snapshot.get('status') != 'ok'):
        return _reject('match_context_source_unavailable_or_racing')
    if value.get('hlboot_sha256') != HLBOOT_SHA256 or value.get('data_cdb_sha256') != DATA_CDB_SHA256:
        return _reject('match_context_build_unverified')
    for key in IDENTITIES:
        if (type(snapshot.get(key)) is not str or not IDENTITY.fullmatch(snapshot[key])
                or value.get(key) != snapshot[key]):
            return _reject('match_context_capture_identity_mismatch')
    uid, own = snapshot.get('local_player_entity_uid'), snapshot.get('local_player_identity')
    if (type(uid) is not int or not 0 < uid < 2**31 or type(own) is not dict
            or own.get('verified') is not True or own.get('role') != 'player'
            or type(own.get('entity_uid')) is not int or own['entity_uid'] != uid
            or own.get('is_ai') is not False or own.get('in_player_list') is not True
            or type(value.get('local_player_entity_uid')) is not int or value['local_player_entity_uid'] != uid):
        return _reject('match_context_local_human_unverified')
    when, before, after, span, parent = (value.get('game_time_seconds'), value.get('time_before'),
        value.get('time_after'), value.get('sample_span_game_seconds'), snapshot.get('game_time_seconds'))
    if (not all(_number(v) for v in (when, before, after, span, parent))
            or abs(when-before) > 1e-7 or not 0 <= after-before <= 5
            or abs(after-before-span) > 1e-7 or not -.025 <= parent-after <= 5):
        return _reject('match_context_clock_mismatch')
    mode, variant, game_type = value.get('native_mode_kind'), value.get('native_mode_variant'), value.get('game_type_kind')
    if ((mode is not None and (type(mode) is not str or mode not in PLAY_MODES))
            or (variant is not None and (type(variant) is not str or variant not in MULTI_MODES))
            or (game_type is not None and (type(game_type) is not str or game_type not in GAME_TYPES))
            or mode in ('Multiplayer', 'Singleplayer') and variant is None
            or mode not in ('Multiplayer', 'Singleplayer') and variant is not None):
        return _reject('match_context_native_enum_invalid')
    if not _count(value.get('native_current_player_count')):
        return _reject('match_context_roster_counts_invalid')
    context = snapshot.get('game_context')
    if type(context) is dict:
        parent_mode, parent_type = context.get('mode'), context.get('game_type')
        if mode is not None and (type(parent_mode) is not dict or parent_mode.get('kind') != mode):
            return _reject('match_context_parent_mode_mismatch')
        if variant is not None and parent_mode.get('args') != [{'kind': variant, 'args': []}]:
            return _reject('match_context_parent_variant_mismatch')
        if game_type is not None and (type(parent_type) is not dict or parent_type.get('kind') != game_type):
            return _reject('match_context_parent_game_type_mismatch')
    elif any(v is not None for v in (mode, variant, game_type)):
        return _reject('match_context_parent_mode_unavailable')
    result = {'schema_version': 1, 'source': SOURCE, 'scope': SCOPE, 'status': value['status'],
        'hlboot_sha256': HLBOOT_SHA256, 'data_cdb_sha256': DATA_CDB_SHA256,
        'local_player_entity_uid': uid, 'atomic': False, **{key: snapshot[key] for key in IDENTITIES},
        'game_time_seconds': when, 'time_before': before, 'time_after': after, 'sample_span_game_seconds': span,
        'native_mode_kind': mode, 'native_mode_variant': variant, 'game_type_kind': game_type,
        'native_current_player_count': value['native_current_player_count'],
        **{key: None for key in COUNT_FIELDS}, 'team_layout': None, 'team_layout_status': 'unavailable',
        'configured_team_layout': CONFIGURED_LAYOUTS.get(variant), 'configured_layout_matches_roster': None,
        'roster_source': 'GameState.startPlayers', 'team_basis': 'Player.teamLeader_or_self',
        'team_id_semantics': 'zero_based_start_roster_group_order',
        'player_count_semantics': 'start_roster_slots_including_ai_if_present',
        'reason_codes': [], 'unknown_fields': []}
    def unknown(field, reason):
        result['status'] = 'partial'
        if field not in result['unknown_fields']:
            result['unknown_fields'].append(field)
        if reason not in result['reason_codes']:
            result['reason_codes'].append(reason)
    for field, allowed in (('reason_codes', REASONS), ('unknown_fields', UNKNOWNS)):
        rows = value.get(field)
        if type(rows) is not list or len(rows) > 64:
            return _reject('match_context_diagnostics_invalid')
        for item in rows:
            if type(item) is not str or item not in allowed:
                return _reject('match_context_diagnostics_invalid')
            if item not in result[field]:
                result[field].append(item)
    if result['unknown_fields']:
        result['status'] = 'partial'
    if mode is None:
        unknown('native_mode_kind', 'match_context_mode_unavailable')
    if game_type is None:
        unknown('game_type_kind', 'match_context_game_type_unavailable')
    count, team_count, own_size, others, team_id = (value.get(key) for key in COUNT_FIELDS)
    if all(v is None for v in (count, team_count, own_size, others, team_id)):
        unknown('roster_counts', 'match_context_roster_or_team_unavailable')
    elif (not all(_count(v) for v in (count, team_count, own_size))
            or type(others) is not list or len(others) > 63 or not all(_count(v) for v in others)
            or type(team_id) is not int or not 0 <= team_id < team_count
            or team_count != 1+len(others) or sum(others)+own_size != count
            or result['native_current_player_count'] is not None and result['native_current_player_count'] > count
            or value.get('roster_source') != 'GameState.startPlayers'
            or value.get('team_basis') != 'Player.teamLeader_or_self'):
        return _reject('match_context_roster_counts_invalid')
    else:
        result.update({key: value[key] for key in COUNT_FIELDS})
        result['native_other_team_sizes'] = list(others)
        layout = 'v'.join(str(size) for size in sorted([own_size, *others], reverse=True))
        configured = result['configured_team_layout']
        agreement = layout == configured if configured is not None else None
        result['configured_layout_matches_roster'] = agreement
        if agreement is False:
            unknown('team_layout', 'match_context_configured_layout_mismatch')
        else:
            result.update(team_layout=layout, team_layout_status='verified_native_roster')
    return result
