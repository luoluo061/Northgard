"""Read native economic caches without executing any Northgard code.

The caller must use the build-checked VM_READ-only reader. All rates are per
GameState.time second, not per wall second or the UI's scaled rate unit.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'runtime'))
from runtime_types import LayoutError
from resource_caps import capture_resource_caps

VERIFIED_HASH = 'db0459d251879698420f58f6819e0ab2f75a3cde6e65611f2ad723f3b4b7ef76'
CORE_FIELDS = {'Food': ('food', 'currentFood'), 'Wood': ('wood', 'currentWood'),
               'Money': ('money', 'currentMoney')}
IDENTITY_FIELDS = ('session_key', 'capture_run_id', 'capture_segment_id')


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise LayoutError('Nonfinite/non-numeric economic value')
    return float(value)


def float_array(h, address):
    if not address or h.object_type(address).name != 'hl.types.ArrayBytes_Float':
        raise LayoutError('Expected Float economic cache array')
    size = h.field(address, 'length')
    if not 0 <= size <= 512:
        raise LayoutError('Invalid economic cache length')
    raw = h.read(h.field(address, 'bytes'), size * 8)
    if len(raw) != size * 8:
        raise LayoutError('Short economic cache array')
    return [finite(x) for x in struct.unpack('<' + 'd' * size, raw)]


def resource_schema(resource_table=None):
    """Canonical source table; row indices are verified cdb indices, not enums.

    A runtime-normalized table can be supplied with explicit ``index`` fields.
    The default is the extraction tied to this exact verified installation.
    """
    if resource_table is None:
        path = HERE.parent / 'static/resource.json'
        raw = path.read_bytes()
        resource_table = json.loads(raw)
        source = {'kind': 'verified_build_static_extraction',
                  'relative_path': 'research/static/resource.json',
                  'file_sha256': hashlib.sha256(raw).hexdigest()}
    else:
        source = {'kind': 'caller_supplied_resource_table'}
    rows = resource_table['rows'] if isinstance(resource_table, dict) else resource_table
    output = []
    for row in rows:
        index = row.get('index', row.get('_row_index'))
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < 512:
            raise LayoutError('Resource row lacks a valid explicit index')
        identifier = row.get('id')
        if not isinstance(identifier, str) or not identifier:
            raise LayoutError('Invalid resource identifier')
        output.append({'id': identifier, 'index': index,
                       'is_productible': row.get('isProductible', row.get('is_productible')),
                       'converts': row.get('props', {}).get('converts', row.get('converts'))})
    output.sort(key=lambda x: x['index'])
    if len({r['id'] for r in output}) != len(output) or len({r['index'] for r in output}) != len(output):
        raise LayoutError('Duplicate resource identifiers or indices')
    canonical = json.dumps(output, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
    return output, hashlib.sha256(canonical).hexdigest(), source


def _counter_map(h, address):
    if not address:
        return {}
    result = h.string_map(address)
    return {key: finite(value) for key, value in result.items()}


def capture_economy(h, res, state, game_time_seconds, resource_table=None, *, identity=None, profile=None,
                    type_base=None):
    """Capture a local player's economic component; returns no process pointers.

    ``identity`` uses the collector's session/run/segment keys. A missing key is
    preserved as missing, and reconciliation will refuse a cross-sample join.
    Native cache timestamps are checked before and after the reads. This guard
    catches observed concurrent mutations, but cannot make process reads atomic.
    """
    if profile is None:
        profile = json.loads((HERE.parent / 'runtime/build_profile.json').read_text(encoding='utf-8'))
    if profile.get('hlboot_sha256') != VERIFIED_HASH:
        raise LayoutError('Economy semantics are not verified for this build')
    if h.object_type(res).name != 'ent.player.ResourcesComponent':
        raise LayoutError('Expected ResourcesComponent')
    table, fingerprint, source = resource_schema(resource_table)
    def limits():
        if type_base is None:
            return {'status': 'unavailable', 'reason_code': 'runtime_type_base_not_supplied', 'resources': {}}
        try:
            return capture_resource_caps(h, res, state, type_base=type_base, profile=profile)
        except (ValueError, OSError, KeyError, TypeError, struct.error) as error:
            return {'status': 'unavailable', 'reason_code': 'resource_limit_read_failed',
                    'error_type': type(error).__name__, 'resources': {}}
    before_time = finite(h.field(state, 'time'))
    caps_before = limits()
    timestamps_before = float_array(h, h.field(res, 'lastResUpdate'))
    stocks_before = {name: finite(h.field(res, fields[0])) for name, fields in CORE_FIELDS.items()}
    production = float_array(h, h.field(res, 'lastResProd'))
    consumption = float_array(h, h.field(res, 'lastResCons'))
    net = {name: finite(h.field(res, fields[1])) for name, fields in CORE_FIELDS.items()}
    counters = _counter_map(h, h.field(res, 'totalProduced'))
    stocks_after = {name: finite(h.field(res, fields[0])) for name, fields in CORE_FIELDS.items()}
    timestamps_after = float_array(h, h.field(res, 'lastResUpdate'))
    caps_after = limits()
    after_time = finite(h.field(state, 'time'))
    races = []
    if timestamps_before != timestamps_after:
        races.append('cache_refreshed_during_read')
    if stocks_before != stocks_after:
        races.append('stock_changed_during_read')
    if after_time < before_time:
        races.append('game_time_rolled_back_during_read')
    if caps_before != caps_after:
        races.append('resource_capacity_changed_during_read')
    slots = []
    for row in table:
        index = row['index']
        updated = timestamps_after[index] if index < len(timestamps_after) else None
        slots.append({**row, 'cached_at_game_time': updated,
                      'age_game_seconds': after_time - updated if updated is not None else None,
                      'raw_production_per_game_second': production[index] if index < len(production) else None,
                      'raw_consumption_per_game_second': consumption[index] if index < len(consumption) else None,
                      'refresh_semantics': ('regular_resource_cycle' if row['is_productible'] else
                                            'not_guaranteed_to_refresh_in_regular_cycle')})
    by_id = {r['id']: r for r in slots}
    resources = {}
    for name in CORE_FIELDS:
        if name not in by_id:
            raise LayoutError('Missing core resource table row: ' + name)
        slot = by_id[name]
        cons = slot['raw_consumption_per_game_second']
        # #13460 writes currentFood AFTER NATURAL_FOOD_RES aggregation, and
        # writes lastResUpdate only after current*. Never sum natural cache slots.
        aggregate = net[name] + cons if cons is not None else None
        cap = caps_after.get('resources', {}).get(name, {})
        resources[name] = {'stock': stocks_after[name],
                           'cached_at_game_time': slot['cached_at_game_time'],
                           'age_game_seconds': slot['age_game_seconds'],
                           'raw_cached_production_per_game_second': slot['raw_production_per_game_second'],
                           'consumption_per_game_second': cons,
                           'net_per_game_second': net[name],
                           'gross_aggregate_per_game_second': aggregate,
                           'gross_aggregate_source': 'native_current_net_plus_native_consumption',
                           'native_net_field': CORE_FIELDS[name][1],
                           'total_produced_counter': counters.get(name, 0.0),
                           'stock_cap': cap.get('stock_cap'),
                           'stock_cap_verified': cap.get('stock_cap_verified', False),
                           'stock_cap_source': cap.get('stock_cap_source', 'unknown'),
                           'stock_cap_status': cap.get('stock_cap_status', caps_after.get('reason_code', 'unavailable')),
                           'integration_order': cap.get('integration_order'),
                           'stock_cap_evidence': cap if cap.get('stock_cap_verified') is True else None,
                           'food_base_cache_is_total': False if name == 'Food' else None}
    output = {'schema_version': 1, 'status': 'read_race' if races else 'ok',
              'read_guard_issues': races, 'source': 'native_economic_caches_read_only',
              'game_build': profile.get('game_build'), 'hlboot_sha256': VERIFIED_HASH,
              'game_time_seconds': finite(game_time_seconds),
              'time_before': before_time, 'time_after': after_time,
              'sample_span_game_seconds': after_time - before_time,
              'atomic': False, 'resource_table_fingerprint': fingerprint,
              'resource_table_source': source, 'raw_resource_slots': slots,
              'cache_lengths': {'updated_at': len(timestamps_after), 'production': len(production),
                                'consumption': len(consumption)},
              'resources': resources, 'total_produced_counters': counters,
              'resource_limits': caps_after,
              'total_produced_semantics': 'Positive addResource amounts with the default ChangeReason; not a complete transaction ledger or solely worker production.',
              'rate_semantics': 'Most recently integrated regular-cycle rates per game second; caches can be stale, snapshots are non-atomic.',
              'aggregation_semantics': 'Food aggregate uses currentFood + Food consumption. Natural-food source slots are retained raw and never summed.',
              'unresolved': ['only_supported_food_capacity_branch_decoded', 'discrete_cost_and_reward_ledger_incomplete',
                             'special_shared_resource_modes_not_reconciled']}
    for key in IDENTITY_FIELDS:
        if identity and key in identity:
            output[key] = identity[key]
    return output
