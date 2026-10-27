"""Conditional game-mechanic estimates, separate from native telemetry.

Only homogeneous ordinary Bear/Raven humans in ordinary singleplayer are
represented. This module never reads a game process, assigns a worker, or
turns an unknown native output into a measured production rate.
"""
from __future__ import annotations

import argparse
import json
import math

MODEL_VERSION = 'ordinary-food-baseline.v1'
MONTH_GAME_SECONDS = 60
BASE_GATHERER_FOOD_PER_GAME_SECOND = 0.4
SUPPORTED_CLANS = ('Bear', 'Raven')
SUPPORTED_CONTEXT = 'ordinary_human_singleplayer_homogeneous_baseline'
BUILD = {'steam_build': '25096976', 'game_version': 'v4.3.29.43387',
    'hlboot_sha256': 'db0459d251879698420f58f6819e0ab2f75a3cde6e65611f2ad723f3b4b7ef76',
    'data_cdb_sha256': '88d4c84b9622ce237533afc8674174aa9006d87a03258cd7e5fd671f930174e3'}


def _population(value):
    if type(value) is not int or not 1 <= value < 2**31:
        raise ValueError('Population must be a positive int32 housing population')
    return value


def ordinary_food_consumption_per_person(population):
    """Base b(P); ordinary units only, before their individual modifiers."""
    population = _population(population)
    return 0.1 * (1 + math.sqrt(population / 46))


def homogeneous_food_consumption(population):
    """C(P) when the food multiplier sum equals the housing population P."""
    return population * ordinary_food_consumption_per_person(population)


def baseline_food_budget(population, gatherers, *, clan='Bear',
                         season='non_winter', winter_festival=False,
                         context=SUPPORTED_CONTEXT):
    """A fixed-population steady-rate estimate, never a full match forecast.

    Conditions: valid uninterrupted Villager->Food work, all ordinary humans
    consume with multiplier one, difficulty/special food/coefCons factors one,
    no other bonuses, maluses, trades, extra income, caps or starvation effects.
    WinterFestival is one explicit Bear consumption exception. Its non-food
    production effect is outside this food-only calculation.
    """
    population = _population(population)
    if population == 2**31 - 1:
        raise ValueError('This budget requires the next population to fit int32 too')
    if type(gatherers) is not int or not 0 <= gatherers <= population:
        raise ValueError('Gatherers must be an integer between zero and population')
    if type(clan) is not str or clan not in SUPPORTED_CLANS:
        raise ValueError('This baseline supports ordinary Bear/Raven only')
    if season not in ('non_winter', 'winter') or type(season) is not str:
        raise ValueError('Season must be non_winter or winter')
    if type(winter_festival) is not bool or winter_festival and clan != 'Bear':
        raise ValueError('WinterFestival is an explicit Bear-only baseline input')
    if context != SUPPORTED_CONTEXT:
        raise ValueError('Unsupported context; do not apply this baseline to other modes')
    winter = season == 'winter'
    ordinary_winter_factor = max(0.6, 0.7 - population / 300)
    winter_penalty_scale = 0.7 if clan == 'Bear' else 1.0
    gatherer_rate = BASE_GATHERER_FOOD_PER_GAME_SECOND
    if winter:
        gatherer_rate *= 1 + winter_penalty_scale * (ordinary_winter_factor - 1)
    consumption_factor = 0.7 if winter and winter_festival else 1.0
    per_person = ordinary_food_consumption_per_person(population) * consumption_factor
    consumption = homogeneous_food_consumption(population) * consumption_factor
    gross = gatherers * gatherer_rate
    minimum = math.ceil(consumption / gatherer_rate)
    # Population growth changes b(P) for the existing population too.
    marginal = (homogeneous_food_consumption(population + 1)
                - homogeneous_food_consumption(population)) * consumption_factor
    return {'model_version': MODEL_VERSION, 'source': 'verified_code_conditional_model',
        'evidence_level': 'code_derived_not_controlled_live_validated',
        'is_native_measurement': False, 'input_context_verified_by_this_module': False,
        'estimate_semantics': 'Month values are fixed-state 60-game-second equivalents, not forecasts.',
        'build': dict(BUILD), 'scope': SUPPORTED_CONTEXT,
        'inputs': {'population': population, 'ordinary_food_consumers': population,
            'gatherers': gatherers, 'clan': clan, 'season': season,
            'winter_festival': winter_festival},
        'model_estimate': {
            'gatherer_gross_food_per_game_second': gatherer_rate,
            'food_consumption_per_person_per_game_second': per_person,
            'total_food_consumption_per_game_second': consumption,
            'gross_food_per_game_month': gross * MONTH_GAME_SECONDS,
            'food_consumption_per_game_month': consumption * MONTH_GAME_SECONDS,
            'net_food_per_game_month': (gross - consumption) * MONTH_GAME_SECONDS,
            'minimum_gatherers_for_nonnegative_steady_food': minimum,
            'steady_food_self_sufficient_is_feasible': minimum <= population,
            'nonfood_workers_after_minimum_food_labor': population - minimum if minimum <= population else None,
            'extra_worker_support_equivalent_at_fixed_population': max(0.0, gatherer_rate / per_person - 1),
            'marginal_total_food_consumption_for_one_more_ordinary_person_per_game_second': marginal,
            'food_to_nonfood_reassignment_delta_food_per_game_second': -gatherer_rate},
        'unknowns': ['actual_canProduce', 'actual_unit_bonuses_and_maluses',
            'actual_individual_settled_production', 'future_population_and_season_changes',
            'payments_events_and_stock_constraints', 'complete_clan_and_mode_rules'],
        'interpretation': 'Conditional steady rates only. Positive flow is not proof that a plan is good or affordable.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--population', type=int, required=True)
    parser.add_argument('--gatherers', type=int, required=True)
    parser.add_argument('--clan', choices=SUPPORTED_CLANS, default='Bear')
    parser.add_argument('--season', choices=('non_winter', 'winter'), default='non_winter')
    parser.add_argument('--winter-festival', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = baseline_food_budget(args.population, args.gatherers, clan=args.clan,
            season=args.season, winter_festival=args.winter_festival)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
