"""Read-only local-player action-state capture for the verified Northgard build.

Sampling observes state transitions, not every mouse click or input command.
No process writes, injection, UI control, or calls into game functions.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'runtime'))
sys.path.insert(0, str(HERE.parents[1] / 'companion' / 'core'))
from live_reader import Probe, Reader, find_pid, objects, string
from runtime_types import LayoutError, SCALAR_FORMATS
from runtime_decode import enum_values, virtual_fields, safe_value
from capture_identity import runtime_session_key, CaptureIdentityTracker
from economy_capture import capture_economy, EconomicCacheNotReady
from operation_capture import capture_operations
from cost_quotes import quote_feast
from map_capture import capture_map
from labor_capture import capture_labor
from growth_capture import capture_growth
from match_context import capture_match_context
from known_zone_capture import capture_known_zone_details


def optional_failure_reasons(component_name, error):
    """Emit only owned static codes, never arbitrary exception messages."""
    reasons = [component_name + '_component_read_failed']
    known = {
        'TownHall is not bound to the local player': 'growth_town_hall_player_reference_conflict',
        'TownHall scope changed during capture': 'growth_town_hall_scope_changed',
        'Own growth building scope changed during capture': 'growth_building_scope_changed',
        'Growth component is not bound to the local player': 'growth_resource_identity_conflict',
        'Local growth identity changed during capture': 'growth_local_identity_changed',
    } if component_name == 'population_growth' else {}
    code = known.get(str(error))
    if code:
        reasons.append(code)
    return reasons


def capture_match_context_component(h, me, state, *, profile, identity):
    """An optional roster context failure must preserve the own-player frame."""
    try:
        return capture_match_context(h, me, state, profile=profile, identity=identity)
    except (ValueError, OSError, KeyError, TypeError, struct.error, OverflowError):
        return {'schema_version': 1, 'status': 'unavailable',
            'source': 'native_match_context_read_only', 'scope': 'no_match_context_data',
            'reason_codes': ['match_context_component_read_failed'],
            'unknown_fields': ['all'], 'atomic': False, **identity}


def capture_operation_component(h, me, state, *, known_zone_ids, owned_building_pointers,
                                identity, profile, clan, game_context):
    """A failed optional payment-state read must not discard basic telemetry."""
    try:
        result = capture_operations(h, me, state, known_zone_ids=known_zone_ids,
            owned_building_pointers=owned_building_pointers, identity=identity, profile=profile)
        result['feast_quote'] = quote_feast(result, clan=clan,
            game_context=game_context, profile=profile)
        return result
    except (ValueError, OSError, KeyError, TypeError, struct.error, OverflowError) as error:
        return {'schema_version': 1, 'status': 'unavailable',
                'source': 'native_own_operation_state_read_only',
                'reason_codes': ['own_operation_component_read_failed'],
                'error_type': type(error).__name__, **identity}


def capture_known_zone_component(h, me, state, *, known_zone_ids, identity, profile,
                                 game_context):
    """Keep optional known-natural failures separate from the economic frame."""
    try:
        return capture_known_zone_details(h, me, state, known_zone_ids=known_zone_ids,
            identity=identity, profile=profile, game_context=game_context)
    except (ValueError, OSError, KeyError, TypeError, struct.error, OverflowError, RuntimeError):
        # Never propagate an exception string, pointer or machine path into
        # the shareable diagnostic. Missing details are not an empty map.
        return {'schema_version': 1, 'status': 'unavailable',
            'source': 'native_discovered_natural_details_read_only',
            'scope': 'no_known_zone_details', 'atomic': False, 'zones': [],
            'reason_codes': ['known_zone_details_component_read_failed'],
            'unknown_fields': ['known_zone_details'], **identity}


class ActionProbe(Probe):
    """Reuse the verified metadata locator and VM_READ-only process reader."""

    def __init__(self, reader, refresh=False):
        super().__init__(reader, refresh)
        self._event_cache = []
        self._event_ptrs = []
        self._event_session = None
        self.identity_tracker = CaptureIdentityTracker()

    def normalize(self, tp, value, depth=0):
        h = self.h
        info = h.type_info(tp)
        if info.kind in SCALAR_FORMATS or info.kind == 0:
            return value
        if not value:
            return None
        if depth > 5:
            raise LayoutError('Action value nesting exceeded')
        if info.kind == 19:  # HashLink null<T> is a boxed dynamic when non-null.
            actual = h.object_type(value)
            if actual.kind in SCALAR_FORMATS:
                return h.dynamic(value)
            return self.normalize(actual.address, value, depth + 1)
        actual = h.object_type(value)
        if actual.name == 'String':
            return string(h, value)
        if actual.kind == 18:
            kind, params = enum_values(h, value)
            return {'kind': kind, 'args': [
                self.normalize(t, v, depth + 1) for t, v in params]}
        if actual.name.startswith('ent.'):
            return self.entity_ref(value, depth=depth + 1)
        return safe_value(h, tp, value, depth)

    def value(self, ptr, field, depth=0):
        h = self.h
        fi = next((f for f in reversed(h.fields(h.object_type(ptr).address))
                   if f.name == field), None)
        if fi is None:
            raise KeyError(h.object_type(ptr).name + '.' + field)
        return self.normalize(fi.type_ptr, h.value_at(ptr + fi.offset, fi.type_ptr), depth)

    def entity_ref(self, ptr, depth=0):
        if not ptr:
            return None
        if depth > 5:
            raise LayoutError('Entity reference nesting exceeded')
        h = self.h
        info = h.object_type(ptr)
        fields = {f.name for f in h.fields(info.address)}
        result = {'entity_type': info.name}
        if '__uid' in fields:
            result['uid'] = h.field(ptr, '__uid')
        if info.name == 'ent.Zone':
            result['zone_id'] = h.field(ptr, 'id')
        elif 'zone' in fields:
            z = h.field(ptr, 'zone')
            result['zone_id'] = h.field(z, 'id') if z else None
        if 'kind' in fields:
            # Zone/prop targets may expose an enum kind; only unit/building
            # kind fields are known to be String. Honor the actual metadata.
            result['kind'] = self.value(ptr, 'kind', depth=depth + 1)
        return result

    def virtual_value(self, address, wanted):
        """Read one materialized virtual field without touching other slots.

        BuildingInf exposes optional fields through dynamic lookup; reading all
        of its virtual slots would fail on an unrelated absent field such as
        assign. We only require id here and never call the dynamic getter.
        """
        h = self.h
        info = h.object_type(address)
        if info.kind != 15:
            raise LayoutError('Expected virtual record')
        fields = h.ptr(info.parameter)
        count = h.unpack(info.parameter + 8, 'i')
        if not 0 <= count <= 512:
            raise LayoutError('Invalid virtual field count')
        for index in range(count):
            field = fields + index * 24
            if h.utf16(h.ptr(field)) != wanted:
                continue
            tp = h.ptr(field + 8)
            slot = h.ptr(address + 24 + index * 8)
            if not slot:
                raise LayoutError('Required virtual field unavailable: ' + wanted)
            return self.normalize(tp, h.value_at(slot, tp))
        raise KeyError('Virtual field missing: ' + wanted)

    def unit(self, ptr):
        h = self.h
        if h.field(ptr, 'owner') != self._me:
            raise LayoutError('Local unit array contains a non-local unit')
        z = h.field(ptr, 'zone')
        tz = h.field(ptr, 'targetZone')
        return {
            'uid': h.field(ptr, '__uid'),
            'kind': string(h, h.field(ptr, 'kind')),
            'zone_id': h.field(z, 'id') if z else None,
            'assigned_job': self.value(ptr, 'assignedJob'),
            'cur_job': self.value(ptr, 'curJob'),
            'assign_to': self.entity_ref(h.field(ptr, 'assignTo')),
            'previous_assign_to': self.entity_ref(h.field(ptr, 'previousAssignTo')),
            'target_zone_id': h.field(tz, 'id') if tz else None,
            'going_to': self.entity_ref(h.field(ptr, 'goingTo')),
            'has_no_work': h.field(ptr, 'hasNoWork'),
            'just_assigned': h.field(ptr, 'justAssigned'),
            'produce': string(h, h.field(ptr, 'produce')),
            'position': {'x': h.field(ptr, 'x'), 'y': h.field(ptr, 'y')},
            'hit_life': h.field(ptr, 'hitLife'),
            'enabled': h.field(ptr, 'enabled'),
            'selected': h.field(ptr, 'selected'),
        }

    def building(self, ptr, zid):
        h = self.h
        kind = string(h, h.field(ptr, 'kind'))
        state_ptr = h.field(ptr, 'state')
        state = None
        if state_ptr:
            # Proxy fields contain the actual data; .obj is its host/marking
            # record, not the backing construction-state value.
            state = {name: self.value(state_ptr, name)
                     for name in ('active', 'activeDate', 'buildStep',
                                  'contains', 'other', 'progress')}
        real_kind = None
        real_inf = h.field(ptr, 'realInf')
        if real_inf:
            real_kind = self.virtual_value(real_inf, 'id')
        creator = h.field(ptr, 'creator')
        return {
            'uid': h.field(ptr, '__uid'), 'kind': kind,
            'base_kind': string(h, h.field(ptr, 'baseKind')),
            'zone_id': zid, 'type': h.object_type(ptr).name,
            'upgraded': h.field(ptr, 'upgraded'),
            'state': state, 'real_kind': real_kind,
            'is_construction': kind == 'Construction',
            'target_kind': (state.get('other') if state else None)
                           if kind == 'Construction' else real_kind,
            'construction_progress': (state.get('progress') if state else None)
                                     if kind == 'Construction' else None,
            'assigned_unit_ids': [h.field(u, '__uid')
                                  for u in objects(h, h.field(ptr, 'assigned'))],
            'position': {'x': h.field(ptr, 'x'), 'y': h.field(ptr, 'y')},
            'hit_life': h.field(ptr, 'hitLife'),
            'wait_repair': h.field(ptr, 'waitRepair'),
            'marked_for_destroy': h.field(ptr, 'isMarkedForDestroy'),
            'creator_is_local': creator == self._me if creator else None,
            'selected': h.field(ptr, 'selected'),
        }

    def native_events(self, state, session):
        h = self.h
        ptrs = objects(h, h.field(state, 'logs'), limit=100000)
        # Reuse decoded append-only prefix. On replacement/truncation, decode
        # all entries again and let the diff layer identify the discontinuity.
        if (session != self._event_session or len(ptrs) < len(self._event_ptrs)
                or ptrs[:len(self._event_ptrs)] != self._event_ptrs):
            self._event_cache = []
            self._event_ptrs = []
        for index in range(len(self._event_ptrs), len(ptrs)):
            fields = virtual_fields(h, ptrs[index])
            kind, params = enum_values(h, fields['k'][1])
            self._event_cache.append({
                'index': index, 'time': fields['time'][1], 'kind': kind,
                'params': [safe_value(h, tp, value) for tp, value in params],
                'source': 'native_GameState.logs',
            })
        self._event_session = session
        self._event_ptrs = ptrs
        return list(self._event_cache)

    def sample(self):
        started = time.monotonic()
        h = self.h
        gt = h.type_info(self.base + self.profile['types']['Game'] * 32)
        static = self.r.u64(self.r.u64(gt.parameter + 56))
        game = h.field(static, 'inst')
        state = h.field(game, 'state') if game else 0
        me = h.field(state, 'me') if state else 0
        if not me:
            return {'schema_version': 2, 'status': 'not_in_match',
                    'source': 'ReadProcessMemory',
                    'captured_at': datetime.now(timezone.utc).isoformat()}
        self._me = me
        t0 = h.field(state, 'time')
        state_uid = h.field(state, '__uid')
        load_time = h.field(state, 'loadTime')
        me_uid = h.field(me, '__uid')
        session = runtime_session_key(pid=self.r.pid, game_address=game,
            state_address=state, local_player_address=me, state_uid=state_uid,
            local_player_uid=me_uid, load_time=load_time,
            game_build=self.profile['game_build'])
        player_list = objects(h, h.field(state, 'players'))
        identity = {'verified': me in player_list and h.object_type(me).name == 'ent.Player',
                    'role': 'player' if me in player_list else 'spectator',
                    'entity_uid': me_uid, 'is_ai': bool(h.field(me, 'aiJob')),
                    'in_player_list': me in player_list}
        if not identity['verified'] or identity['is_ai']:
            return {'schema_version': 2, 'status': 'unsupported_player_identity',
                    'local_player_identity': identity, 'session_key': session,
                    'captured_at': datetime.now(timezone.utc).isoformat()}
        base = super().sample()
        if base.get('status') != 'ok':
            return base
        own_units = [(u, self.unit(u)) for u in objects(h, h.field(me, 'units'))]
        units = [row for _, row in own_units]
        zones = []
        owned_zones = []
        buildings = []
        owned_buildings = []
        owned_building_pointers = []
        for zone in objects(h, h.field(state, 'zones')):
            if h.field(zone, 'owner') != me:
                continue
            zid = h.field(zone, 'id')
            zones.append(zid)
            owned_zones.append((zone, zid))
            for building in objects(h, h.field(zone, 'buildings')):
                owned_building_pointers.append((building, zid))
                row = self.building(building, zid)
                buildings.append(row)
                owned_buildings.append((building, row))
        if len({u['uid'] for u in units}) != len(units):
            raise LayoutError('Duplicate unit UID in local unit array')
        if len({b['uid'] for b in buildings}) != len(buildings):
            raise LayoutError('Duplicate building UID in owned zones')
        events = self.native_events(state, session)
        identities = self.identity_tracker.observe(session, h.field(state, 'time'), events)
        context = {'mode': self.value(state, 'mode'),
                   'game_type': self.value(state, 'gameType'),
                   'difficulty': h.field(state, 'difficulty'),
                   'biome': string(h, h.field(state, 'biome')),
                   'bifrost_data_present': bool(h.field(state, 'bifrostData')),
                   'editor_present': bool(h.field(state, 'editor'))}
        match_context = capture_match_context_component(h, me, state,
            profile=self.profile, identity=identities)
        operations = capture_operation_component(h, me, state,
            known_zone_ids=set(base.get('discovered_zone_ids', [])) | set(zones),
            owned_building_pointers=owned_building_pointers,
            identity=identities, profile=self.profile, clan=base.get('clan'),
            game_context=context)
        known_details = capture_known_zone_component(h, me, state,
            known_zone_ids=base.get('discovered_zone_ids', []),
            identity=identities, profile=self.profile, game_context=context)
        try:
            map_observation = capture_map(h, me, state,
                known_zone_ids=base.get('discovered_zone_ids', []),
                identity=identities, profile=self.profile)
        except (ValueError, OSError, KeyError, TypeError, struct.error) as error:
            map_observation = {'schema_version': 1, 'status': 'unavailable',
                'reason_code': 'discovered_map_component_unavailable',
                'error_type': type(error).__name__, **identities}
        try:
            economy = capture_economy(h, h.field(me, 'res'), state,
                h.field(state, 'time'), identity=identities, profile=self.profile,
                type_base=self.base)
            economy['special_shared_resource_mode'] = context['bifrost_data_present']
            if context['bifrost_data_present']:
                economy['status'] = 'unsupported_resource_mode'
        except EconomicCacheNotReady:
            economy = {'schema_version': 1, 'status': 'not_ready',
                'source': 'native_economic_caches_read_only',
                'hlboot_sha256': self.profile.get('hlboot_sha256'),
                'reason_codes': ['economic_cache_not_initialized'], **identities}
        except (ValueError, OSError, KeyError, struct.error) as error:
            economy = {'status': 'read_error', 'error_type': type(error).__name__,
                       'error': str(error), **identities}
        optional = {}
        for component_name, read_component in (
                ('labor', lambda: capture_labor(h, me, state, own_units=own_units,
                    owned_zones=owned_zones, owned_buildings=owned_buildings,
                    profile=self.profile, identity=identities)),
                ('population_growth', lambda: capture_growth(h, me, state,
                    owned_building_pointers, profile=self.profile, identity=identities))):
            try:
                optional[component_name] = read_component()
            except (ValueError, OSError, KeyError, TypeError, struct.error, OverflowError) as error:
                optional[component_name] = {'schema_version': 1, 'status': 'unavailable',
                    'reason_codes': optional_failure_reasons(component_name, error),
                    'error_type': type(error).__name__, **identities}
        t1 = h.field(state, 'time')
        if (h.field(static, 'inst') != game or h.field(game, 'state') != state
                or h.field(state, 'me') != me
                or h.field(state, 'loadTime') != load_time
                or t1 < t0):
            raise LayoutError('Match/load changed during action capture')
        base.update({
            'schema_version': 2, 'session_key': session,
            'research_only': True,
            'scope': 'local-player entities; audited discovered natural details; complete native event log for post-match research only',
            'game_time_seconds': t1,
            'sample_span_game_seconds': t1 - t0,
            'sample_duration_wall_seconds': time.monotonic() - started,
            'local_player_entity_uid': me_uid,
            'local_player_identity': identity,
            'game_context': context,
            'match_context': match_context,
            'economy': economy,
            'operations': operations,
            'map_observation': map_observation,
            'known_zone_details': known_details,
            **optional,
            'session_game_finished': h.field(state, 'gameFinished'),
            'local_player_game_over': h.field(me, 'gameOver'),
            'local_player_has_conceded': h.field(me, 'hasConceded'),
            'local_player_defeated_time': h.field(me, 'defeatedTime'),
            'owned_zone_ids': sorted(zones),
            'units': units, 'buildings': buildings, 'native_events': events,
            'capture_limits': [
                'State observations are non-atomic and do not record every input command.',
                'State-diff event time is an interval between valid samples.',
                'A disappearing construction is not proof of player cancellation.',
                'Resource deltas are net changes, not isolated action costs.',
                'Session key detects object/load changes; time rollback and log-prefix changes must also split review segments.',
            ],
        })
        base.update(identities)
        return base

    snapshot = sample


def snapshot():
    with Reader(find_pid()) as reader:
        return ActionProbe(reader).sample()


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pid', type=int)
    p.add_argument('--seconds', type=float, default=10)
    p.add_argument('--interval', type=float, default=0.5)
    p.add_argument('--refresh', action='store_true')
    p.add_argument('--append', action='store_true')
    p.add_argument('--out', type=Path, default=HERE / 'captures' / 'actions.ndjson')
    a = p.parse_args()
    if not 0 <= a.seconds <= 14400 or not 0.1 <= a.interval <= 60:
        p.error('Duration must be 0..14400 seconds; interval must be 0.1..60')
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with Reader(a.pid or find_pid()) as reader:
        probe = ActionProbe(reader, a.refresh)
        end = time.monotonic() + a.seconds
        seq = 0
        with a.out.open('a' if a.append else 'w', encoding='utf-8') as out:
            while True:
                begin = time.monotonic()
                try:
                    row = probe.sample()
                except (OSError, ValueError, KeyError, struct.error) as error:
                    row = {'schema_version': 2, 'status': 'read_error',
                           'captured_at': datetime.now(timezone.utc).isoformat(),
                           'source': 'ReadProcessMemory', 'error': str(error)}
                row['sequence'] = seq
                seq += 1
                out.write(json.dumps(row, ensure_ascii=False) + '\n')
                out.flush()
                latest = a.out.with_suffix('.latest.json')
                tmp = latest.with_suffix('.tmp')
                tmp.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
                tmp.replace(latest)
                print(json.dumps({
                    k: row.get(k) for k in ('sequence', 'status',
                    'game_time_seconds', 'sample_duration_wall_seconds',
                    'population', 'population_cap', 'error')
                }, ensure_ascii=False), flush=True)
                if time.monotonic() >= end:
                    break
                time.sleep(max(0, a.interval - (time.monotonic() - begin)))


if __name__ == '__main__':
    main()
