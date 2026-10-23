"""Read roster counts and the local team, never other players' game state.

Build evidence: PlayMode #922, MultiMode #923, GameType #924;
Player.get_team #4803 returns teamLeader or self. GameState.getPlayersByTeam
#7554 groups players/startPlayers using that identity. No getter is executed.
Player counts include AI slots if present; they are not human-player counts.
"""
from __future__ import annotations

import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'runtime'))
from runtime_types import LayoutError
from live_reader import objects
from runtime_decode import enum_values

SOURCE = 'native_match_context_read_only'
SCOPE = 'match_roster_counts_and_local_team'
HLBOOT_SHA256 = 'db0459d251879698420f58f6819e0ab2f75a3cde6e65611f2ad723f3b4b7ef76'
DATA_CDB_SHA256 = '88d4c84b9622ce237533afc8674174aa9006d87a03258cd7e5fd671f930174e3'
IDENTITIES = ('session_key', 'capture_run_id', 'capture_segment_id')
PLAY_MODES = frozenset(('Single', 'Multiplayer', 'Campaign', 'Conquest', 'Singleplayer', 'Bifrost'))
MULTI_MODES = frozenset(('Duel', 'FreeForAll', 'Team2V2', 'Team3V3', 'Team2V2V2', 'Team4V4', 'Team2V2V2V2', 'FreeTeams'))
GAME_TYPES = frozenset(('Standard', 'Private', 'Ranked'))
CONFIGURED_LAYOUTS = {'Duel': '1v1', 'Team2V2': '2v2', 'Team3V3': '3v3',
    'Team2V2V2': '2v2v2', 'Team4V4': '4v4', 'Team2V2V2V2': '2v2v2v2'}
COUNT_FIELDS = ('native_player_count', 'native_team_count', 'native_own_team_size',
                'native_other_team_sizes', 'native_local_team_id')
ERRORS = (OSError, ValueError, KeyError, TypeError, OverflowError)


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value < 2**53:
        raise LayoutError('Invalid match-context game clock')
    return value


def _enum(h, pointer, expected, allowed):
    if not pointer or h.object_type(pointer).name != 'enum:' + expected:
        raise LayoutError('Unexpected match-context enum')
    name, args = enum_values(h, pointer)
    if name not in allowed:
        raise LayoutError('Unsupported match-context constructor')
    return name, args


def _mode(h, state):
    name, args = _enum(h, h.field(state, 'mode'), 'PlayMode', PLAY_MODES)
    if name in ('Multiplayer', 'Singleplayer'):
        if len(args) != 1 or h.type_info(args[0][0]).name != 'enum:MultiMode':
            raise LayoutError('Invalid native multiplayer variant')
        variant, extra = _enum(h, args[0][1], 'MultiMode', MULTI_MODES)
        if extra:
            raise LayoutError('Unexpected native variant arguments')
        return name, variant
    expected_args = {'Single': 0, 'Campaign': 1, 'Conquest': 2, 'Bifrost': 1}
    if len(args) != expected_args[name]:
        raise LayoutError('Invalid native mode arguments')
    # Campaign/conquest IDs and other argument values are deliberately not read.
    return name, None


def _game_type(h, state):
    name, args = _enum(h, h.field(state, 'gameType'), 'GameType', GAME_TYPES)
    if args:
        raise LayoutError('Unexpected native game-type arguments')
    return name


def derive_roster_counts(roster, leaders, me):
    """Pure #4803/#7554 grouping. Pointer identities stay internal.

    Team IDs are local zero-based group ordinals in start-roster order. They
    identify neither accounts nor a common match across different computers.
    Unresolved/transient leader references yield no guessed team assignment.
    """
    if (not 1 <= len(roster) <= 64 or len(set(roster)) != len(roster)
            or me not in roster or set(leaders) != set(roster)):
        raise LayoutError('Incomplete native starting roster')
    keys = {p: leaders[p] or p for p in roster}
    if any(key not in keys or keys[key] != key for key in keys.values()):
        raise LayoutError('Native team leader does not close within starting roster')
    groups = []
    group_keys = []
    for player in roster:
        key = keys[player]
        if key not in group_keys:
            group_keys.append(key)
            groups.append([])
        groups[group_keys.index(key)].append(player)
    # A non-null self leader is how getTeamMembers distinguishes a team from
    # a solo player (#4798). Refuse a transient multi-member null-leader group.
    if any(len(group) > 1 and leaders[key] != key for key, group in zip(group_keys, groups)):
        raise LayoutError('Incomplete native team-leader initialization')
    own = group_keys.index(keys[me])
    sizes = [len(group) for group in groups]
    return {'native_player_count': len(roster), 'native_team_count': len(groups),
        'native_own_team_size': sizes[own], 'native_local_team_id': own,
        'native_other_team_sizes': [size for index, size in enumerate(sizes) if index != own]}


def capture_match_context(h, me, state, *, profile, identity):
    """Optional pointer-free context; failures do not invalidate core capture.

    Other roster objects are inspected for their type and teamLeader field
    only. No names, account IDs, clans, units, resources or allied state enter
    this component. startPlayers and current players remain distinct counts.
    """
    result = {'schema_version': 1, 'source': SOURCE, 'scope': SCOPE, 'status': 'unavailable',
        'hlboot_sha256': HLBOOT_SHA256, 'data_cdb_sha256': DATA_CDB_SHA256,
        'atomic': False, 'local_player_entity_uid': None,
        'game_time_seconds': None, 'time_before': None, 'time_after': None, 'sample_span_game_seconds': None,
        'native_mode_kind': None, 'native_mode_variant': None, 'game_type_kind': None,
        'native_current_player_count': None, **{key: None for key in COUNT_FIELDS},
        'team_layout': None, 'team_layout_status': 'unavailable', 'configured_team_layout': None,
        'configured_layout_matches_roster': None, 'roster_source': 'GameState.startPlayers',
        'team_basis': 'Player.teamLeader_or_self', 'team_id_semantics': 'zero_based_start_roster_group_order',
        'player_count_semantics': 'start_roster_slots_including_ai_if_present',
        'reason_codes': [], 'unknown_fields': []}
    for key in IDENTITIES:
        if type(identity) is dict and key in identity:
            result[key] = identity[key]
    def unknown(field, code):
        if field not in result['unknown_fields']:
            result['unknown_fields'].append(field)
        if code not in result['reason_codes']:
            result['reason_codes'].append(code)
    try:
        if (type(profile) is not dict or profile.get('hlboot_sha256') != HLBOOT_SHA256
                or profile.get('data_cdb_sha256') != DATA_CDB_SHA256):
            unknown('all', 'match_context_unverified_build')
            return result
        if (h.object_type(state).name != 'GameState' or h.object_type(me).name != 'ent.Player'
                or h.field(state, 'me') != me or h.field(me, 'aiJob')):
            unknown('all', 'match_context_local_human_unverified')
            return result
        uid = h.field(me, '__uid')
        if type(uid) is not int or not 0 < uid < 2**31:
            raise LayoutError('Invalid local context entity identity')
        result['local_player_entity_uid'] = uid
        before = _number(h.field(state, 'time'))
        result['game_time_seconds'] = result['time_before'] = before
        current_pointer = h.field(state, 'players')
        current = objects(h, current_pointer, limit=64)
        if (not 1 <= len(current) <= 64 or len(set(current)) != len(current) or me not in current
                or any(h.object_type(p).name != 'ent.Player' for p in current)):
            raise LayoutError('Current player roster does not verify local player')
        result['native_current_player_count'] = len(current)
    except ERRORS:
        unknown('all', 'match_context_identity_or_clock_unavailable')
        return result
    try:
        result['native_mode_kind'], result['native_mode_variant'] = _mode(h, state)
        result['configured_team_layout'] = CONFIGURED_LAYOUTS.get(result['native_mode_variant'])
    except ERRORS:
        unknown('native_mode_kind', 'match_context_mode_unavailable')
    try:
        result['game_type_kind'] = _game_type(h, state)
    except ERRORS:
        unknown('game_type_kind', 'match_context_game_type_unavailable')
    roster_pointer = None
    roster = leaders = None
    try:
        roster_pointer = h.field(state, 'startPlayers')
        roster = objects(h, roster_pointer, limit=64)
        if any(h.object_type(p).name != 'ent.Player' for p in roster) or not set(current) <= set(roster):
            raise LayoutError('Starting player roster does not close')
        leaders = {p: h.field(p, 'teamLeader') for p in roster}
        result.update(derive_roster_counts(roster, leaders, me))
        sizes = [result['native_own_team_size'], *result['native_other_team_sizes']]
        layout = 'v'.join(str(size) for size in sorted(sizes, reverse=True))
        configured = result['configured_team_layout']
        agreement = configured == layout if configured is not None else None
        result['configured_layout_matches_roster'] = agreement
        if agreement is not False:
            result.update(team_layout=layout, team_layout_status='verified_native_roster')
        else:
            unknown('team_layout', 'match_context_configured_layout_mismatch')
    except ERRORS:
        unknown('roster_counts', 'match_context_roster_or_team_unavailable')
    try:
        after = _number(h.field(state, 'time'))
        changed = (after < before or h.field(state, 'me') != me or h.field(me, '__uid') != uid
            or h.field(state, 'players') != current_pointer
            or objects(h, current_pointer, limit=64) != current)
        if roster is not None and leaders is not None:
            changed = changed or (h.field(state, 'startPlayers') != roster_pointer
                or objects(h, roster_pointer, limit=64) != roster
                or {p: h.field(p, 'teamLeader') for p in roster} != leaders)
        if result['native_mode_kind'] is not None:
            changed = changed or _mode(h, state) != (result['native_mode_kind'], result['native_mode_variant'])
        if result['game_type_kind'] is not None:
            changed = changed or _game_type(h, state) != result['game_type_kind']
        result.update(time_after=after, sample_span_game_seconds=after-before)
        if changed:
            result.update(status='read_race', team_layout=None, team_layout_status='unavailable',
                configured_layout_matches_roster=None, **{key: None for key in COUNT_FIELDS})
            unknown('all', 'match_context_changed_during_read')
            return result
    except ERRORS:
        result.update(status='read_race', team_layout=None, team_layout_status='unavailable',
            configured_layout_matches_roster=None, **{key: None for key in COUNT_FIELDS})
        unknown('all', 'match_context_read_guard_unavailable')
        return result
    result['status'] = 'partial' if result['unknown_fields'] else 'ok'
    return result
