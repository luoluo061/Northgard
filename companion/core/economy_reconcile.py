"""Conservative offline consistency checks for native rates and resource stocks.

No stock derivative is relabelled as production. Residuals are unexplained
differences, not automatically attributed to clicks, fees or rewards.
"""
from __future__ import annotations

import math

CORE = ('Food', 'Wood', 'Money')
IDENTITY = ('session_key', 'capture_run_id', 'capture_segment_id')
CAPACITY_BUILD = 'db0459d251879698420f58f6819e0ab2f75a3cde6e65611f2ad723f3b4b7ef76'
POTENTIAL_TRANSACTIONS = {'construction_started', 'construction_removed_unresolved',
                          'construction_started_observed', 'construction_removed_after_cancel_command',
                          'construction_cancelled', 'Colonize', 'Feast', 'HireWarchief',
                          'HireMercenaries', 'LearnTech', 'LearnFreeTech', 'UpgradeBuilding',
                          'BuyResource', 'SellResource', 'LearnKnowledge', 'trade',
                          'resource_transaction'}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _verified_food_capacity(a, b, x, y, name):
    """Only the verified native Food setter can justify a clipping correction.

    In this build a cycle consumes first, then adds production. The setter
    mutates the positive amount before totalProduced counts it. Other resource
    setters, changing caps, and unverified numeric guesses keep legacy refusal.
    """
    if name != 'Food' or any(row.get('hlboot_sha256') != CAPACITY_BUILD for row in (a, b)):
        return None
    if any(row.get('stock_cap_verified') is not True
           or row.get('stock_cap_source') != 'verified_native_getMax_formula'
           or row.get('integration_order') != 'consumption_then_production'
           or not _number(row.get('stock_cap')) or row['stock_cap'] <= 0 for row in (x, y)):
        return None
    if x['stock_cap'] != y['stock_cap']:
        return None
    return y['stock_cap']


def _economic(row):
    if 'economy' not in row:
        return row
    economy = row['economy']
    result = {**{key: row.get(key) for key in (*IDENTITY, 'status', 'game_time_seconds')}, **economy}
    if row.get('status') != 'ok':
        result['status'] = 'invalid_parent_snapshot'
    if any(row.get(key) and economy.get(key) and row[key] != economy[key] for key in IDENTITY):
        result['status'] = 'conflicting_parent_identity'
    return result


def reconcile_pair(before, after, *, events=(), max_gap=5.0, max_cache_age=1.25,
                   max_sample_span=0.2, absolute_tolerance=0.025, relative_tolerance=0.002):
    """Compare two same-segment snapshots, using EACH resource's native clock.

    An accepted result means endpoint/cache/counter/stock consistency under the
    stated interval assumptions. It does not prove that no unobserved action
    occurred. For skipped updates, the rate integral is an endpoint estimate,
    with an uncertainty interval and an explicit unobserved-change caveat.
    """
    a, b = _economic(before), _economic(after)
    reasons = []
    for key in IDENTITY:
        if not a.get(key) or not b.get(key):
            reasons.append('missing_' + key)
        elif a[key] != b[key]:
            reasons.append('different_' + key)
    for key in ('hlboot_sha256', 'resource_table_fingerprint'):
        if not a.get(key) or a.get(key) != b.get(key):
            reasons.append('unverified_or_changed_' + key)
    if a.get('status') != 'ok' or b.get('status') != 'ok':
        reasons.append('invalid_or_racing_snapshot')
    for row in (a, b):
        span = row.get('sample_span_game_seconds')
        if not _number(span) or not 0 <= span <= max_sample_span:
            reasons.append('sample_span_out_of_bounds')
    t0, t1 = a.get('game_time_seconds'), b.get('game_time_seconds')
    if not _number(t0) or not _number(t1):
        reasons.append('missing_game_time')
        dt = None
    else:
        dt = t1 - t0
        if dt < 0:
            reasons.append('game_time_rollback')
        elif dt > max_gap:
            reasons.append('sampling_gap_too_large')
    result = {'schema_version': 1, 'status': 'not_reconcilable' if reasons else 'pending',
              'reasons': sorted(set(reasons)), 'game_time_start': t0, 'game_time_end': t1,
              'sample_elapsed_game_seconds': dt, 'resources': {},
              'interpretation': 'Consistency check only. Residuals are not causal action attribution.',
              'assumptions': ['Only Food/Wood/Money ordinary resource integration is supported.',
                              'Endpoint rates do not reveal every intermediate refresh.',
                              'totalProduced counts default-reason positive additions, not all transaction types.']}
    for key in IDENTITY:
        result[key] = b.get(key)
    if reasons:
        return result
    if a.get('special_shared_resource_mode') or b.get('special_shared_resource_mode'):
        result.update(status='not_reconcilable', reasons=['special_shared_resource_mode'])
        return result
    relevant_events = []
    for event in events:
        if any(event.get(key) and event[key] != b.get(key) for key in IDENTITY):
            continue
        if event.get('kind') not in POTENTIAL_TRANSACTIONS:
            continue
        event_time = event.get('time', event.get('game_time_seconds', event.get('observed_at')))
        event_interval = event.get('time_interval', {})
        event_start = event_interval.get('start_game_seconds')
        event_end = event_interval.get('end_game_seconds')
        if _number(event_time) and t0 < event_time <= t1:
            relevant_events.append({'kind': event['kind'], 'time': event_time})
        elif (_number(event_start) and _number(event_end) and
              event_start < t1 and event_end > t0):
            relevant_events.append({'kind': event['kind'], 'time_interval': event_interval})
    for name in CORE:
        x, y = a.get('resources', {}).get(name, {}), b.get('resources', {}).get(name, {})
        issues = []
        required = ('stock', 'cached_at_game_time', 'age_game_seconds', 'net_per_game_second',
                    'gross_aggregate_per_game_second', 'consumption_per_game_second', 'total_produced_counter')
        if any(not _number(row.get(key)) for row in (x, y) for key in required):
            result['resources'][name] = {'status': 'not_reconcilable', 'reasons': ['missing_native_values']}
            continue
        if any(abs(row['gross_aggregate_per_game_second'] - row['consumption_per_game_second']
                   - row['net_per_game_second']) > 1e-9 for row in (x, y)):
            issues.append('native_rates_inconsistent')
        cache_dt = y['cached_at_game_time'] - x['cached_at_game_time']
        stock_delta = y['stock'] - x['stock']
        counter_delta = y['total_produced_counter'] - x['total_produced_counter']
        if any(row['age_game_seconds'] < -max_sample_span or row['age_game_seconds'] > max_cache_age for row in (x, y)):
            issues.append('stale_or_future_cache')
        if cache_dt < 0:
            issues.append('cache_time_rollback')
        if counter_delta < -absolute_tolerance:
            issues.append('cumulative_counter_decreased')
        if any(row['stock'] <= absolute_tolerance for row in (x, y)):
            issues.append('stock_floor_or_starvation_possible')
        capacity = _verified_food_capacity(a, b, x, y, name)
        if (_number(x.get('stock_cap')) and _number(y.get('stock_cap'))
                and x['stock_cap'] != y['stock_cap']):
            issues.append('stock_capacity_changed_in_interval')
        if capacity is None and any(row.get('stock_at_cap') is True or (_number(row.get('stock_cap')) and
               row['stock'] >= row['stock_cap'] - absolute_tolerance) for row in (x, y)):
            issues.append('stock_cap_or_clipping_possible')
        if relevant_events:
            issues.append('potential_discrete_transaction_in_interval')
        if cache_dt == 0:
            if abs(stock_delta) > absolute_tolerance or abs(counter_delta) > absolute_tolerance:
                issues.append('stock_or_counter_changed_without_regular_refresh')
            status = 'paused' if dt == 0 and not issues else ('not_reconcilable' if issues else 'no_new_cycle')
            result['resources'][name] = {'status': status, 'reasons': issues, 'native_cycle_elapsed_game_seconds': 0,
                                         'observed_stock_delta': stock_delta,
                                         'production_integral_estimate': 0.0, 'consumption_integral_estimate': 0.0,
                                         'net_integral_estimate': 0.0, 'residual_unexplained': stock_delta,
                                         'positive_addition_counter_delta': counter_delta,
                                         'positive_counter_minus_production_estimate': counter_delta}
            continue
        if dt == 0:
            issues.append('cache_changed_while_game_time_paused')
        if cache_dt > max_gap + max_cache_age * 2:
            issues.append('native_cycle_gap_too_large')
        # B's rate was used to integrate the cycle ending at B.cached_at.
        # For multiple unknown intermediate cycles this is an estimate only.
        production = y['gross_aggregate_per_game_second'] * cache_dt
        consumption = y['consumption_per_game_second'] * cache_dt
        predicted = y['net_per_game_second'] * cache_dt
        low = min(x['net_per_game_second'], y['net_per_game_second']) * cache_dt
        high = max(x['net_per_game_second'], y['net_per_game_second']) * cache_dt
        potential_predicted = predicted
        applied_production = production
        clipped_production = 0.0
        capacity_model_used = False
        if capacity is not None:
            # Do not model starvation, a cap decrease or a negative-production
            # branch using the ordinary consumption-then-positive-add formula.
            if x['stock'] > capacity + absolute_tolerance or y['stock'] > capacity + absolute_tolerance:
                issues.append('stock_exceeds_verified_capacity')
            elif x['stock'] - consumption < 0:
                issues.append('consumption_floor_within_cycle_possible')
            elif production < 0 or consumption < 0:
                issues.append('negative_native_gross_or_consumption')
            else:
                applied_production = min(production, max(0.0, capacity - (x['stock'] - consumption)))
                clipped_production = production - applied_production
                predicted = applied_production - consumption
                capacity_model_used = True
        residual = stock_delta - predicted
        counter_residual = counter_delta - applied_production
        tolerance = absolute_tolerance + relative_tolerance * max(abs(predicted), abs(stock_delta), abs(production), 1)
        rate_changed = any(abs(x[key] - y[key]) > 1e-9 for key in
                           ('net_per_game_second', 'gross_aggregate_per_game_second', 'consumption_per_game_second'))
        if rate_changed:
            issues.append('endpoint_rates_changed_unknown_transition_time')
        if abs(residual) > tolerance:
            issues.append('stock_residual_exceeds_tolerance')
        if abs(counter_residual) > tolerance:
            issues.append('positive_counter_differs_from_rate_integral')
        if any(row['gross_aggregate_per_game_second'] < -1e-9 or
               row['consumption_per_game_second'] < -1e-9 for row in (x, y)):
            issues.append('negative_native_gross_or_consumption')
        result['resources'][name] = {
            'status': 'not_reconcilable' if issues else 'accepted', 'reasons': issues,
            'native_cycle_start_game_time': x['cached_at_game_time'],
            'native_cycle_end_game_time': y['cached_at_game_time'],
            'native_cycle_elapsed_game_seconds': cache_dt,
            'observed_stock_delta': stock_delta, 'production_integral_estimate': production,
            'production_applied_integral_estimate': applied_production,
            'production_clipped_integral_estimate': clipped_production,
            'consumption_integral_estimate': consumption, 'net_integral_estimate': predicted,
            'potential_net_integral_estimate': potential_predicted,
            'verified_capacity_model_used': capacity_model_used,
            'verified_stock_cap': capacity if capacity_model_used else None,
            'endpoint_net_integral_range': [low, high],
            'residual_unexplained': residual, 'absolute_acceptance_tolerance': tolerance,
            'residual_before_capacity_adjustment': stock_delta - potential_predicted,
            'positive_addition_counter_delta': counter_delta,
            'positive_counter_minus_production_estimate': counter_delta - production,
            'positive_counter_minus_applied_production_estimate': counter_residual,
            'potential_discrete_transactions': relevant_events,
            'intermediate_refreshes_observed': False,
            'uncertainty': 'Intermediate rates/actions can change between samples; endpoint range is not a guaranteed mathematical bound.'}
    statuses = {r['status'] for r in result['resources'].values()}
    result['status'] = ('accepted' if statuses == {'accepted'} else 'paused' if statuses == {'paused'} else
                        'no_new_cycle' if statuses <= {'no_new_cycle', 'paused'} else 'not_reconcilable')
    return result


def reconcile_stream(samples, **options):
    """Preserve acquisition order and split on every invalid/discontinuous row.

    Never sort by game time: sorting would conceal a reload/rollback or join
    samples across a failed read. No predecessor is reused after a gap.
    """
    previous = None
    for index, row in enumerate(samples):
        valid = row.get('status') == 'ok' and _economic(row).get('status') == 'ok'
        if not valid:
            previous = None
            yield {'schema_version': 1, 'status': 'not_reconcilable',
                   'reasons': ['invalid_row_breaks_continuity'], 'sample_index': index,
                   'resources': {}}
            continue
        if previous is not None:
            events = row.get('native_events', []) + row.get('own_success_events', []) + row.get('action_events', [])
            result = reconcile_pair(previous, row, events=events, **options)
            result['sample_index'] = index
            yield result
        previous = row


def summarize_reconciliation(results):
    """Counts and inspected residuals, with no blanket assertion of closure."""
    from collections import Counter
    rows = list(results)
    counts = Counter(row['status'] for row in rows)
    by_resource = {}
    for name in CORE:
        blocks = [row['resources'][name] for row in rows if name in row.get('resources', {})]
        reasons = Counter(reason for block in blocks for reason in block.get('reasons', []))
        residuals = [block['residual_unexplained'] for block in blocks if _number(block.get('residual_unexplained'))]
        accepted = [block for block in blocks if block['status'] == 'accepted']
        by_resource[name] = {
            'statuses': dict(Counter(block['status'] for block in blocks)),
            'reasons': dict(reasons),
            'accepted_native_cycle_seconds': sum(block.get('native_cycle_elapsed_game_seconds', 0) for block in accepted),
            'accepted_production_estimate_sum': sum(block.get('production_integral_estimate', 0) for block in accepted),
            'accepted_production_applied_estimate_sum': sum(block.get('production_applied_integral_estimate',
                                                                       block.get('production_integral_estimate', 0)) for block in accepted),
            'accepted_production_clipped_estimate_sum': sum(block.get('production_clipped_integral_estimate', 0) for block in accepted),
            'accepted_consumption_estimate_sum': sum(block.get('consumption_integral_estimate', 0) for block in accepted),
            'max_absolute_inspected_residual': max(map(abs, residuals), default=None),
            'max_absolute_accepted_residual': max((abs(block['residual_unexplained']) for block in accepted), default=None)}
    return {'schema_version': 1, 'intervals': len(rows), 'statuses': dict(counts), 'resources': by_resource,
            'notice': 'Accepted sums cover accepted intervals only, not the whole match. Estimates assume unobserved intermediate rates did not change.'}


def main():
    import argparse
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    def rows():
        with args.capture.open(encoding='utf-8') as handle:
            for line in handle:
                if line.strip():
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        yield {'status': 'malformed_capture_line'}
    results = list(reconcile_stream(rows()))
    summary = summarize_reconciliation(results)
    output = args.out or args.capture.with_suffix('.economy_reconciliation.json')
    output.write_text(json.dumps({'summary': summary, 'intervals': results}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
