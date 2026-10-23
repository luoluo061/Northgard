"""Pure, conservative spending quotes for the verified Northgard build.

No process access or calls to game functions. A quote is a prediction of the
ordinary UI action, not proof of a submitted or successful transaction.
"""
from __future__ import annotations

import math

HLBOOT_SHA256 = 'db0459d251879698420f58f6819e0ab2f75a3cde6e65611f2ad723f3b4b7ef76'
DATA_CDB_SHA256 = '88d4c84b9622ce237533afc8674174aa9006d87a03258cd7e5fd671f930174e3'
REQUIRED_FEAST_INPUTS = (
    'feast_count', 'free_feast', 'free_feast_count', 'last_feast',
    'free_feast_time', 'team_leader_is_self', 'has_relic_goat',
    'has_relic_chalice', 'has_trial_faith', 'hunt_cost_reduction',
    'goat_cost_reduction', 'living_legend_cost_reduction',
)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def quote_feast(operations, *, clan, game_context, profile):
    """Quote the next ordinary Feast only for fully observed Bear single-player FFA.

    ``operations['feast']`` must contain every REQUIRED_FEAST_INPUTS field.
    Team, relic, TrialFaith and nonzero modifier branches are deliberately
    unsupported in v1, even where another branch might make them irrelevant.
    Missing data never silently becomes zero or False.
    """
    result = {
        'schema_version': 1,
        'status': 'unsupported',
        'source': 'verified_native_getFeastCost_formula',
        'native_function': {'name': 'ent.player.ResourcesComponent.getFeastCost', 'findex': 13528},
        'scope': 'next_ordinary_feast_cost_only; not_action_availability_or_applied_transaction',
        'required_inputs': list(REQUIRED_FEAST_INPUTS),
        'cost': [],
        'reasons': [],
    }
    reasons = result['reasons']
    if not isinstance(profile, dict) or profile.get('hlboot_sha256') != HLBOOT_SHA256 or profile.get('data_cdb_sha256') != DATA_CDB_SHA256:
        reasons.append('unverified_build_profile')
    if clan != 'Bear':
        reasons.append('unsupported_clan')
    mode = game_context.get('mode') if isinstance(game_context, dict) else None
    expected_mode = {'kind': 'Singleplayer', 'args': [{'kind': 'FreeForAll', 'args': []}]}
    if mode != expected_mode:
        reasons.append('unsupported_or_unknown_game_mode')
    if (not isinstance(game_context, dict) or
            game_context.get('bifrost_data_present') is not False or
            game_context.get('editor_present') is not False):
        reasons.append('unknown_or_special_mode_context')
    if not isinstance(operations, dict) or operations.get('status') != 'ok':
        reasons.append('invalid_operations_snapshot')
    feast = operations.get('feast') if isinstance(operations, dict) else None
    if not isinstance(feast, dict):
        reasons.append('missing_feast_inputs')
        return result
    # Evidence is a scalar whitelist too: do not propagate unexpected objects,
    # strings, pointers, or non-JSON NaN/Infinity from malformed source input.
    result['input_evidence'] = {
        key: feast[key] if type(feast[key]) is bool or _number(feast[key]) else None
        for key in REQUIRED_FEAST_INPUTS if key in feast
    }
    for key in REQUIRED_FEAST_INPUTS:
        if key not in feast:
            reasons.append('missing_' + key)
    for key in ('feast_count', 'free_feast', 'free_feast_count'):
        value = feast.get(key)
        if type(value) is not int or not 0 <= value <= 10000:
            reasons.append('invalid_' + key)
    for key in ('last_feast', 'free_feast_time', 'hunt_cost_reduction',
                'goat_cost_reduction', 'living_legend_cost_reduction'):
        if not _number(feast.get(key)):
            reasons.append('invalid_' + key)
    for key in ('team_leader_is_self', 'has_relic_goat', 'has_relic_chalice', 'has_trial_faith'):
        if type(feast.get(key)) is not bool:
            reasons.append('invalid_' + key)
    if feast.get('team_leader_is_self') is not True:
        reasons.append('team_leader_branch_not_supported')
    for key in ('has_relic_goat', 'has_relic_chalice', 'has_trial_faith'):
        if feast.get(key) is not False:
            reasons.append('special_branch_' + key)
    for key in ('hunt_cost_reduction', 'goat_cost_reduction', 'living_legend_cost_reduction'):
        if _number(feast.get(key)) and feast[key] != 0:
            reasons.append('nonzero_modifier_' + key)
    if reasons:
        result['reasons'] = sorted(set(reasons))
        return result
    count = feast['feast_count']
    if feast['free_feast'] > 0:
        amount = 0
        branch = 'observed_free_feast'
    else:
        # Native std.math_round is floor(x + .5) for positive cost, not Python's
        # ties-to-even round(). The source first forms 10*pow(1.5,n)*10.
        try:
            tens = (10.0 * math.pow(1.5, count) * 10.0) / 10.0
        except OverflowError:
            result['reasons'] = ['cost_overflow']
            return result
        if not math.isfinite(tens) or tens > 214748364:
            result['reasons'] = ['cost_outside_native_i32_range']
            return result
        amount = math.floor(tens + .5) * 10
        branch = 'paid_feast_zero_modifiers'
    result.update(status='verified', cost=[{'resource': 'Food', 'amount': amount}],
                  formula_branch=branch, formula='10 * native_math_round(10 * 1.5 ** feast_count)',
                  build_evidence={'hlboot_sha256': HLBOOT_SHA256, 'data_cdb_sha256': DATA_CDB_SHA256})
    return result
