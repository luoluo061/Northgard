"""Conservative, offline links between sampled own operations and stock changes.

This is an observation ledger, not input interception or a complete accounting
system. Stored payment records, prior quotes and observed residuals remain
separate evidence layers. No input object is retained or copied wholesale.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

try:
    from .economy_reconcile import reconcile_pair
except ImportError:
    from economy_reconcile import reconcile_pair

IDENTITY = ('session_key', 'capture_run_id', 'capture_segment_id')
RESOURCES = ('Food', 'Wood', 'Money', 'Stone', 'Iron', 'Lore')
CORE = RESOURCES[:3]
TOKEN = re.compile(r'^[A-Za-z0-9_.:-]{1,128}$')
ECON_NUMBERS = ('game_time_seconds', 'time_before', 'time_after', 'sample_span_game_seconds')
RESOURCE_NUMBERS = ('stock', 'cached_at_game_time', 'age_game_seconds', 'net_per_game_second',
                    'gross_aggregate_per_game_second', 'consumption_per_game_second',
                    'total_produced_counter', 'stock_cap')
FEAST_COUNTS = ('feast_count', 'free_feast', 'free_feast_count')
NATIVE_CONTEXT = frozenset(('HireWarchief', 'HireMercenaries', 'UpgradeUnit', 'UpgradeBuilding',
                            'BuyResource', 'SellResource', 'LearnTech', 'LearnFreeTech'))
STATE_CONTEXT = frozenset(('unit_kind_changed', 'building_upgrade_state_changed',
                           'construction_cancelled', 'construction_removed_after_cancel_command',
                           'construction_removed_unresolved'))


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _uid(value):
    return type(value) is int and 0 <= value < 2 ** 53


def _token(value):
    return value if type(value) is str and TOKEN.fullmatch(value) else None


def _kind(value):
    return value if type(value) is str and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]{0,95}', value) else None


def _cost(value):
    """Unknown, duplicate, negative or non-player-resource costs never mean zero."""
    if type(value) is not list or len(value) > len(RESOURCES):
        return None
    output = []
    seen = set()
    for row in value:
        if (type(row) is not dict or row.get('resource') not in RESOURCES
                or row['resource'] in seen or not _number(row.get('amount')) or row['amount'] < 0):
            return None
        seen.add(row['resource'])
        output.append({'resource': row['resource'], 'amount': row['amount']})
    return output


def _sum_cost(*costs):
    if any(cost is None for cost in costs):
        return None
    amounts = {}
    for cost in costs:
        for row in cost:
            amounts[row['resource']] = amounts.get(row['resource'], 0) + row['amount']
    return [{'resource': r, 'amount': amounts[r]} for r in RESOURCES if r in amounts]


def _normalize(snapshot):
    if (type(snapshot) is not dict or snapshot.get('status') != 'ok'
            or snapshot.get('observation_type') != 'PlayerObservation'
            or any(_token(snapshot.get(key)) is None for key in IDENTITY)
            or not _uid(snapshot.get('local_player_entity_uid'))
            or not _number(snapshot.get('game_time_seconds')) or snapshot['game_time_seconds'] < 0):
        return None
    out = {key: snapshot[key] for key in (*IDENTITY, 'local_player_entity_uid', 'game_time_seconds')}
    out['status'] = 'ok'
    out['stocks'] = {r: snapshot.get('stocks', {}).get(r) if _number(snapshot.get('stocks', {}).get(r)) else None for r in RESOURCES} if type(snapshot.get('stocks')) is dict else dict.fromkeys(RESOURCES)
    out['sample_span_game_seconds'] = snapshot.get('sample_span_game_seconds') if _number(snapshot.get('sample_span_game_seconds')) else None
    for field in ('owned_zone_ids', 'discovered_zone_ids'):
        values = snapshot.get(field)
        out[field] = {x for x in values if _uid(x)} if type(values) is list and len(values) <= 4096 else set()
    out['buildings'] = {}
    for row in snapshot.get('buildings', []) if type(snapshot.get('buildings')) is list else []:
        if type(row) is dict and _uid(row.get('uid')) and _uid(row.get('zone_id')):
            if row['uid'] in out['buildings']:
                return None
            out['buildings'][row['uid']] = {'uid': row['uid'], 'zone_id': row['zone_id'],
                'is_construction': row.get('is_construction') is True,
                'kind': _kind(row.get('kind')), 'target_kind': _kind(row.get('target_kind'))}
    # Only bounded scalar fields required by the existing reconciler.
    raw = snapshot.get('economy')
    econ = {'status': 'unknown', 'resources': {}}
    if type(raw) is dict:
        econ['status'] = raw.get('status') if raw.get('status') in ('ok', 'read_race', 'rejected') else 'unknown'
        for key in (*IDENTITY, 'hlboot_sha256', 'resource_table_fingerprint'):
            econ[key] = _token(raw.get(key))
        for key in ECON_NUMBERS:
            econ[key] = raw.get(key) if _number(raw.get(key)) else None
        econ['special_shared_resource_mode'] = raw.get('special_shared_resource_mode') is True
        for resource in CORE:
            source = raw.get('resources', {}).get(resource, {}) if type(raw.get('resources')) is dict else {}
            if type(source) is not dict:
                source = {}
            row = {key: source.get(key) if _number(source.get(key)) else None for key in RESOURCE_NUMBERS}
            row['stock_cap_verified'] = source.get('stock_cap_verified') is True
            row['stock_cap_source'] = 'verified_native_getMax_formula' if source.get('stock_cap_source') == 'verified_native_getMax_formula' else 'unknown'
            row['integration_order'] = 'consumption_then_production' if source.get('integration_order') == 'consumption_then_production' else None
            row['stock_at_cap'] = source.get('stock_at_cap') is True
            econ['resources'][resource] = row
    out['economy'] = econ
    out['operations'] = _operations(snapshot.get('operations'), out)
    out['own_events'] = []
    values = snapshot.get('own_events')
    if type(values) is list and len(values) <= 100000:
        for event in values:
            if (type(event) is dict and event.get('actor') == 'self'
                    and event.get('source') == 'native_own_success_event'
                    and _uid(event.get('index')) and _number(event.get('time'))
                    and event['time'] >= 0 and _token(event.get('kind'))):
                if any(key in event and event[key] != out[key] for key in IDENTITY):
                    continue
                out['own_events'].append({'index': event['index'], 'time': event['time'],
                    'kind': event['kind'], 'zone_id': event.get('zone_id') if _uid(event.get('zone_id')) else None})
    return out


def _operations(value, snapshot):
    result = {'valid': False, 'unknown': {'all'}, 'colonizations': {}, 'building_costs': {}, 'feast': {}, 'feast_quote': None}
    if (type(value) is not dict or value.get('schema_version') != 1
            or value.get('source') != 'native_own_operation_state_read_only'
            or value.get('status') not in ('ok', 'partial')
            or any(value.get(key) != snapshot[key] for key in IDENTITY)
            or value.get('local_player_entity_uid') != snapshot['local_player_entity_uid']
            or not _number(value.get('game_time_seconds'))
            or abs(value['game_time_seconds'] - snapshot['game_time_seconds']) > 1.0
            or not _number(value.get('sample_span_game_seconds'))
            or not 0 <= value['sample_span_game_seconds'] <= .2
            or not all(_number(value.get(key)) for key in ('time_before', 'time_after'))
            or not 0 <= value['time_after'] - value['time_before'] <= .2
            or abs(value['sample_span_game_seconds'] - (value['time_after'] - value['time_before'])) > 1e-6):
        return result
    fields = value.get('unknown_fields')
    if type(fields) is not list or len(fields) > 1000:
        return result
    result['unknown'] = {x for x in fields if type(x) is str and len(x) <= 128}
    result['valid'] = True
    for field, key in (('colonizations', 'zone_id'), ('building_costs', 'uid')):
        rows = value.get(field)
        if type(rows) is not list or len(rows) > 4096:
            result['unknown'].add(field)
            continue
        for row in rows:
            if type(row) is not dict or not _uid(row.get(key)) or row[key] in result[field]:
                result['unknown'].add(field)
                continue
            if field == 'colonizations':
                item = {'zone_id': row[key], 'started_by_self': row.get('started_by_self') if type(row.get('started_by_self')) is bool else None}
                for cost_key in ('paid_cost', 'extra_paid_cost'):
                    status = row.get(cost_key + '_record_status')
                    status = status if status in ('stored_record', 'no_record', 'unavailable') else 'unavailable'
                    if status == 'no_record' and row.get(cost_key) is not None:
                        status = 'unavailable'
                    item[cost_key + '_record_status'] = status
                    item[cost_key] = _cost(row.get(cost_key)) if status == 'stored_record' else None
            else:
                item = {'uid': row[key], 'zone_id': row.get('zone_id') if _uid(row.get('zone_id')) else None,
                        'was_free': row.get('was_free') if type(row.get('was_free')) is bool else None,
                        'cost_record_status': row.get('cost_record_status') if row.get('cost_record_status') in ('stored_record', 'not_stored', 'unknown') else 'unknown',
                        'cost': _cost(row.get('cost'))}
            result[field][row[key]] = item
    source = value.get('feast')
    if type(source) is dict:
        result['feast'] = {key: source.get(key) if _uid(source.get(key)) else None for key in FEAST_COUNTS}
        result['feast']['last_feast'] = source.get('last_feast') if _number(source.get('last_feast')) else None
    quote = value.get('feast_quote')
    if (type(quote) is dict and quote.get('status') == 'verified'
            and quote.get('source') == 'verified_native_getFeastCost_formula'
            and quote.get('formula_branch') in ('paid_feast_zero_modifiers', 'observed_free_feast')):
        cost = _cost(quote.get('cost'))
        if (cost is not None and len(cost) == 1 and cost[0]['resource'] == 'Food'
                and type(cost[0]['amount']) is int and cost[0]['amount'] <= 2147483640
                and (cost[0]['amount'] == 0) == (quote['formula_branch'] == 'observed_free_feast')):
            result['feast_quote'] = {'cost': cost, 'formula_branch': quote['formula_branch']}
    return result


def _component(operations, name):
    return operations['valid'] and not any(x == 'all' or x == name or x.startswith(name + '.') for x in operations['unknown'])


def _event_key(event):
    return event['index'], event['kind'], event['time']


def _action_context(action, before, after):
    if action is None:
        return True, [], []
    if type(action) is not dict or action.get('record_kind') != 'interval':
        return False, [], []
    if any(key in action and action[key] != after[key] for key in IDENTITY):
        return False, [], []
    interval = action.get('time_interval')
    if (type(interval) is not dict or action.get('skipped_samples_since_previous_valid', 0) != 0
            or not all(_number(interval.get(key)) for key in ('start_game_seconds', 'end_game_seconds'))
            or abs(interval['start_game_seconds'] - before['game_time_seconds']) > 1e-7
            or abs(interval['end_game_seconds'] - after['game_time_seconds']) > 1e-7):
        return False, [], []
    states = []
    for row in action.get('events', []) if type(action.get('events')) is list else []:
        if type(row) is not dict or _token(row.get('kind')) is None:
            continue
        entity = row.get('entity') if type(row.get('entity')) is dict else {}
        states.append({'kind': row['kind'], 'uid': entity.get('uid') if _uid(entity.get('uid')) else None})
    native = []
    for row in action.get('new_native_events', []) if type(action.get('new_native_events')) is list else []:
        if type(row) is not dict or any(row.get(key) != after[key] for key in IDENTITY):
            continue
        if row.get('actor') != 'self' or row.get('source') != 'native_own_success_event':
            continue
        if (row.get('local_player_entity_uid') != after['local_player_entity_uid']
                or not _uid(row.get('index')) or not _number(row.get('time')) or not _token(row.get('kind'))):
            continue
        native.append({'index':row['index'], 'kind':row['kind'], 'time':row['time']})
    return True, states, native


class OperationLedger:
    def __init__(self, *, max_gap=5.0, absolute_tolerance=.025):
        if not _number(max_gap) or not 0 < max_gap <= 60:
            raise ValueError('max_gap must be positive and at most 60 game seconds')
        if not _number(absolute_tolerance) or not 0 < absolute_tolerance <= .1:
            raise ValueError('absolute_tolerance must be positive and at most .1 resource unit')
        self.max_gap = max_gap
        self.tolerance = absolute_tolerance
        self.previous = None
        self.sample_index = 0

    def _base(self, row, kind, reason=None):
        result = {'schema_version':1, 'record_kind':kind, 'sample_index':self.sample_index,
                  'source':'offline_own_operation_observation_ledger', 'atomic':False,
                  'complete_transaction_ledger':False}
        if row:
            result.update({key: row[key] for key in (*IDENTITY, 'local_player_entity_uid', 'game_time_seconds')})
        if reason:
            result['reason'] = reason
        return result

    def feed(self, projected_snapshot, action=None):
        self.sample_index += 1
        after = _normalize(projected_snapshot)
        if after is None:
            self.previous = None
            return self._base(None, 'ledger_gap', 'invalid_or_unidentified_player_observation')
        before = self.previous
        self.previous = after
        if before is None:
            return self._base(after, 'ledger_baseline', 'initial_or_after_gap')
        if any(before[key] != after[key] for key in (*IDENTITY, 'local_player_entity_uid')):
            return self._base(after, 'ledger_baseline', 'identity_changed')
        dt = after['game_time_seconds'] - before['game_time_seconds']
        if dt < 0:
            return self._base(after, 'ledger_baseline', 'game_time_rollback')
        previous_events = [_event_key(e) for e in before['own_events']]
        current_events = [_event_key(e) for e in after['own_events']]
        if current_events[:len(previous_events)] != previous_events:
            return self._base(after, 'ledger_baseline', 'native_history_replaced')
        valid_action, states, native = _action_context(action, before, after)
        if dt > self.max_gap or not valid_action:
            return self._base(after, 'ledger_gap', 'sampling_gap' if dt > self.max_gap else 'action_interval_or_identity_mismatch')
        result = self._base(after, 'ledger_interval')
        result['time_interval'] = {'start_game_seconds':before['game_time_seconds'], 'end_game_seconds':after['game_time_seconds'], 'duration_game_seconds':dt}
        result['stock_changes'] = {resource: after['stocks'][resource] - before['stocks'][resource]
            if all(_number(r['stocks'][resource]) for r in (before, after)) else None for resource in RESOURCES}
        new_native = after['own_events'][len(before['own_events']):]
        if action is not None:
            allowed = {_event_key(e) for e in native}
            new_native = [e for e in new_native if _event_key(e) in allowed]
        candidates = self._candidates(before, after, states, new_native, action is not None)
        context = sum(s['kind'] in STATE_CONTEXT for s in states) + sum(e['kind'] in NATIVE_CONTEXT for e in new_native)
        payments = [c for c in candidates if c['payment_effect'] == 'possible_debit']
        if len(payments) > 1:
            for candidate in payments:
                candidate['blockers'].append('multiple_potential_payments_in_interval')
        if context or any(c['payment_effect'] == 'unknown_refund_or_removal' for c in candidates):
            for candidate in payments:
                candidate['blockers'].append('other_unpriced_operation_or_refund_in_interval')
        if any(not _number(r['sample_span_game_seconds']) or not 0 <= r['sample_span_game_seconds'] <= .2 for r in (before, after)):
            for candidate in payments:
                candidate['blockers'].append('non_atomic_sample_span_not_bounded')
        reconciliation = reconcile_pair(before, after, max_gap=self.max_gap)
        result['continuous_economy'] = self._economy_summary(reconciliation)
        links = {}
        for resource in RESOURCES:
            block = result['continuous_economy'].get(resource)
            links[resource] = {'stock_delta':result['stock_changes'][resource],
                              'status':'stock_change_only_continuous_flow_unknown',
                              'unexplained_residual':None}
            if block:
                safe = self._safe_residual(block)
                residual = block.get('residual_unexplained')
                links[resource].update(economic_component_stock_delta=block.get('observed_stock_delta'),
                    unexplained_residual=residual, continuous_model_usable_for_cost_match=safe,
                    status='continuous_economy_consistent' if safe and _number(residual) and abs(residual) <= self.tolerance else 'unexplained_or_uncertain_residual')
        for candidate in candidates:
            candidate['resource_matches'] = {}
            if candidate['payment_effect'] != 'possible_debit':
                candidate['match_status'] = 'not_a_new_payment' if candidate['payment_effect'] == 'none_on_completion' else 'ambiguous'
                continue
            if candidate['cost'] is None:
                candidate['match_status'] = 'unknown_cost'
                continue
            amounts = {r['resource']: r['amount'] for r in candidate['cost']}
            for resource in RESOURCES:
                amount = amounts.get(resource, 0)
                block = result['continuous_economy'].get(resource)
                if resource in CORE:
                    residual = block.get('residual_unexplained') if block else None
                    safe = bool(block and self._safe_residual(block))
                    if block and block.get('production_clipped_integral_estimate', 0) and amount:
                        safe = False
                        candidate['blockers'].append('capacity_and_discrete_action_order_unknown')
                    matches = safe and _number(residual) and abs(residual + amount) <= self.tolerance
                    candidate['resource_matches'][resource] = {'expected_discrete_change':-amount,
                        'observed_residual':residual,
                        'status':'amount_matches_usable_residual' if matches else 'unexplained_or_uncertain'}
                    if not matches:
                        candidate['blockers'].append('core_resource_residual_unknown_or_does_not_match')
                elif resource in amounts:
                    delta = result['stock_changes'][resource]
                    matches = _number(delta) and abs(delta + amount) <= self.tolerance
                    candidate['resource_matches'][resource] = {'expected_discrete_change':-amount,
                        'observed_stock_delta':delta,
                        'status':'stock_delta_matches_stored_cost_without_continuous_calibration' if matches else 'stock_change_not_an_isolated_cost'}
                    candidate['blockers'].append('noncore_continuous_flow_not_calibrated')
            candidate['blockers'] = sorted(set(candidate['blockers']))
            if not candidate['blockers']:
                candidate['match_status'] = 'cost_matches_observed_residual'
                for resource, amount in amounts.items():
                    links[resource].update(status='cost_matches_observed_residual', matched_candidate_id=candidate['candidate_id'],
                        matched_discrete_change=-amount,
                        unexplained_residual=links[resource]['unexplained_residual'] + amount)
            elif candidate['blockers'] == ['noncore_continuous_flow_not_calibrated']:
                candidate['match_status'] = 'partial_cost_match'
            else:
                candidate['match_status'] = 'ambiguous'
        result.update(operation_candidates=candidates, resource_links=links,
                      other_unpriced_operation_count=context,
                      interpretation='Matching amounts connect stored state evidence to observed residuals; they do not prove every intermediate input or transaction was captured.')
        return result

    def _candidates(self, before, after, states, new_native, has_action):
        output = []
        a, b = before['operations'], after['operations']
        def candidate(kind, target, cost=None, effect='possible_debit', evidence=(), blockers=()):
            row = {'candidate_id':f'ledger:{self.sample_index}:{len(output)}', 'kind':kind,
                   'target':target, 'cost':cost, 'payment_effect':effect,
                   'evidence':list(evidence), 'blockers':list(blockers)}
            output.append(row)
            return row
        # A missing paid map must not erase an otherwise visible start/end.
        # Structural/ownership state and price-record availability are separate.
        colonization_states_known = all(o['valid'] and not any(
            field in o['unknown'] for field in ('all', 'colonizations',
                'colonizations.progress', 'colonizations.started_by_self')) for o in (a, b))
        if colonization_states_known:
            for zone, row in b['colonizations'].items():
                old = a['colonizations'].get(zone)
                if row['started_by_self'] is not True or (old and old['started_by_self'] is not False):
                    continue
                blockers, components = [], []
                if not _component(a, 'colonizations'):
                    blockers.append('previous_colonization_cost_state_unavailable')
                known_cost = False
                for key in ('paid_cost', 'extra_paid_cost'):
                    status = row[key + '_record_status']
                    if status == 'stored_record':
                        known_cost = True
                        components.append(row[key])
                        if old and old[key + '_record_status'] != 'no_record':
                            blockers.append('paid_record_preexisted_or_previous_record_unknown')
                    elif status == 'no_record':
                        components.append([])
                    else:
                        components.append(None)
                        blockers.append('paid_cost_record_unavailable')
                if not known_cost:
                    components = [None]
                cost = _sum_cost(*components)
                if zone not in (before['owned_zone_ids'] | before['discovered_zone_ids']):
                    blockers.append('zone_scope_changed_in_interval')
                candidate('colonization_started', {'zone_id':zone}, cost,
                    evidence=('started_by_self_transition', 'native_stored_colonization_payment_records'), blockers=blockers)
            for zone, old in a['colonizations'].items():
                if old['started_by_self'] is not True or b['colonizations'].get(zone, {}).get('started_by_self') is True:
                    continue
                complete = zone in after['owned_zone_ids'] and zone not in before['owned_zone_ids']
                candidate('colonization_completed' if complete else 'colonization_ended_unresolved',
                    {'zone_id':zone}, effect='none_on_completion' if complete else 'unknown_refund_or_removal',
                    evidence=('colonization_state_ended',), blockers=() if complete else ('disappearance_does_not_prove_cancel_or_refund',))
        for uid, row in after['buildings'].items():
            if not row['is_construction'] or uid in before['buildings']:
                continue
            blockers, cost = [], None
            stored = b['building_costs'].get(uid)
            if not _component(b, 'building_costs') or not stored or stored['zone_id'] != row['zone_id']:
                blockers.append('stored_building_cost_unavailable')
            elif stored['was_free'] is True:
                cost = []  # An explicit native free flag, never missing cost -> zero.
            elif stored['was_free'] is False and stored['cost_record_status'] == 'stored_record':
                cost = stored['cost']
            else:
                blockers.append('stored_building_cost_or_free_flag_unknown')
            if row['zone_id'] not in before['owned_zone_ids'] or row['zone_id'] not in after['owned_zone_ids']:
                blockers.append('building_ownership_scope_changed')
            if has_action and not any(s['kind'] == 'construction_started_observed' and s['uid'] == uid for s in states):
                blockers.append('construction_state_event_missing')
            candidate('construction_started', {'building_uid':uid, 'zone_id':row['zone_id'], 'building_kind':row['target_kind']}, cost,
                evidence=('new_owned_construction_uid', 'native_stored_building_cost_or_free_flag'), blockers=blockers)
        for state in states:
            if state['kind'] == 'construction_completed' or state['kind'] in STATE_CONTEXT and state['kind'].startswith('construction_'):
                if state['uid'] not in before['buildings']:
                    continue
                completed = state['kind'] == 'construction_completed'
                candidate(state['kind'], {'building_uid':state['uid']},
                    effect='none_on_completion' if completed else 'unknown_refund_or_removal',
                    evidence=('state_diff_removal_or_completion',), blockers=() if completed else ('disappearance_or_cancel_intent_not_an_isolated_refund',))
        feast_events = [e for e in new_native if e['kind'] == 'Feast']
        if feast_events:
            blockers, cost = [], None
            fa, fb = a['feast'], b['feast']
            if not _component(a, 'feast') or not _component(b, 'feast'):
                blockers.append('feast_component_unavailable')
            paid = fb.get('feast_count') - fa.get('feast_count') if all(_uid(f.get('feast_count')) for f in (fa, fb)) else None
            free = fb.get('free_feast_count') - fa.get('free_feast_count') if all(_uid(f.get('free_feast_count')) for f in (fa, fb)) else None
            if len(feast_events) != 1 or (paid, free) not in ((1, 0), (0, 1)):
                blockers.append('not_exactly_one_observed_feast_counter_increment')
            quote = a['feast_quote']
            if quote:
                cost = quote['cost']
                if (quote['formula_branch'] == 'observed_free_feast') != ((paid, free) == (0, 1)):
                    blockers.append('quote_branch_does_not_match_counter_increment')
            else:
                blockers.append('prior_verified_feast_quote_unavailable')
            last = fb.get('last_feast')
            if not _number(last) or not before['game_time_seconds'] <= last <= after['game_time_seconds']:
                blockers.append('feast_timestamp_outside_observed_interval')
            for event in feast_events:
                if not math.floor(before['game_time_seconds']) <= event['time'] <= after['game_time_seconds']:
                    blockers.append('native_success_timestamp_outside_observed_interval')
            candidate('feast_started', {}, cost,
                evidence=('new_own_native_feast_success', 'feast_counter_transition', 'prior_verified_cost_quote'), blockers=blockers)
        return output

    @staticmethod
    def _economy_summary(result):
        fields = ('status', 'reasons', 'observed_stock_delta', 'net_integral_estimate',
                  'production_integral_estimate', 'production_applied_integral_estimate',
                  'production_clipped_integral_estimate', 'consumption_integral_estimate',
                  'residual_unexplained', 'positive_counter_minus_production_estimate',
                  'positive_counter_minus_applied_production_estimate', 'verified_capacity_model_used',
                  'native_cycle_elapsed_game_seconds', 'verified_stock_cap')
        output = {}
        for resource in CORE:
            raw = result.get('resources', {}).get(resource, {})
            row = {key: deepcopy(raw[key]) for key in fields if key in raw}
            if result.get('reasons'):
                row['model_guard_reasons'] = list(result['reasons'])
            output[resource] = row
        return output

    def _safe_residual(self, block):
        allowed = {'stock_residual_exceeds_tolerance', 'stock_or_counter_changed_without_regular_refresh'}
        if block.get('model_guard_reasons') or any(reason not in allowed for reason in block.get('reasons', [])):
            return False
        counter = block.get('positive_counter_minus_applied_production_estimate', block.get('positive_counter_minus_production_estimate'))
        return _number(block.get('residual_unexplained')) and _number(counter) and abs(counter) <= self.tolerance
