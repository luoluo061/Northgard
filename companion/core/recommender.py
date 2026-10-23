"""Offline short-horizon budget comparator; NOT a Northgard simulator or live reader.

All rates are per game second, relative action deltas include reassigned workers'
lost production. Missing/null observations are never silently replaced with zero.
Costs and rate changes are checked at their event times, including reserve floors.
Season-crossing forecasts require explicit baseline AND action rate segments.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

EPS = 1e-8


class NeedsData(Exception):
    pass


def number(value, field):
    if value is None:
        raise NeedsData(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field}: expected a finite number")
    return float(value)


def vector(raw, resources, field, *, nonnegative=False, complete=True):
    if not isinstance(raw, dict):
        raise NeedsData(field)
    keys = resources if complete else raw.keys()
    result = {}
    for key in keys:
        value = number(raw.get(key), f"{field}.{key}")
        if nonnegative and value < 0:
            raise ValueError(f"{field}.{key}: must be nonnegative")
        result[key] = value
    unknown = set(raw) - set(resources)
    if unknown:
        raise NeedsData("snapshot.stocks: " + ", ".join(sorted(unknown)))
    return result


def segments(raw, horizon, resources, field, rate_fields, required_boundary=None):
    if not isinstance(raw, list) or not raw:
        raise NeedsData(field)
    result, previous = [], 0.0
    for index, part in enumerate(raw):
        prefix = f"{field}[{index}]"
        end = number(part.get("until_seconds"), prefix + ".until_seconds")
        if end <= previous:
            raise ValueError(prefix + ": segment ends must strictly increase")
        item = {"end": end}
        for key in rate_fields:
            item[key] = vector(part.get(key), resources, prefix + "." + key)
        result.append(item)
        previous = end
    if previous < horizon:
        raise NeedsData(field + ": must cover deadline")
    if required_boundary is not None and required_boundary > 0:
        if not any(abs(part["end"] - required_boundary) <= EPS for part in result):
            raise NeedsData(field + ": must split at known season boundary")
    return result


def rates_at(parts, time, field):
    return next(part[field] for part in parts if part["end"] > time)


def project(stocks, floors, baseline_parts, action_parts, delay, costs, horizon):
    """Piecewise-linear stocks: extrema occur before/after each event boundary."""
    resources = list(stocks)
    levels, minimum = dict(stocks), dict(stocks)
    violations = {}

    def check(time, interval_start=None, interval_levels=None):
        for res in resources:
            minimum[res] = min(minimum[res], levels[res])
            if levels[res] + EPS < floors[res] and res not in violations:
                first_time, first_stock = time, levels[res]
                if interval_levels is not None and interval_levels[res] >= floors[res]:
                    fraction = (interval_levels[res] - floors[res]) / (interval_levels[res] - levels[res])
                    first_time = interval_start + fraction * (time - interval_start)
                    first_stock = floors[res]
                violations[res] = {"resource": res, "time_seconds": first_time,
                                   "stock": first_stock, "floor": floors[res]}

    boundaries = {0.0, horizon}
    boundaries.update(part["end"] for part in baseline_parts if part["end"] < horizon)
    boundaries.update(part["end"] for part in action_parts if part["end"] < horizon)
    if 0 < delay < horizon:
        boundaries.add(delay)
    boundaries.update(time for time, _ in costs if 0 <= time <= horizon)
    previous = 0.0
    check(0.0)
    for time in sorted(boundaries):
        if time > previous:
            before_interval = dict(levels)
            midpoint = (previous + time) / 2
            base = rates_at(baseline_parts, midpoint, "net_rates")
            phase = "during_setup_rate_delta" if midpoint < delay else "after_start_rate_delta"
            delta = rates_at(action_parts, midpoint, phase)
            for res in resources:
                levels[res] += (base[res] + delta[res]) * (time - previous)
            check(time, previous, before_interval)
        for debit_time, debit in costs:
            if debit_time == time:
                for res, amount in debit.items():
                    levels[res] -= amount
                check(time)
        previous = time
    return {"projected_stocks": levels, "minimum_stocks": minimum,
            "reserve_violations": list(violations.values())}


def evaluate(payload):
    """Return JSON-serializable candidates; this function never modifies its input."""
    snapshot, goal = payload["snapshot"], payload["goal"]
    horizon = number(goal.get("deadline_seconds"), "goal.deadline_seconds")
    target = number(goal.get("target"), "goal.target")
    if horizon <= 0 or target < 0:
        raise ValueError("deadline_seconds must be positive; target must be nonnegative")
    raw_stocks = snapshot.get("stocks")
    resources = list(raw_stocks) if isinstance(raw_stocks, dict) else []
    goal_resource = goal["resource"]
    if goal_resource not in resources:
        resources.append(goal_resource)
    assumptions = payload.get("assumptions", {})
    shared_missing = []
    if assumptions.get("rate_units") != "per_game_second":
        shared_missing.append("assumptions.rate_units=per_game_second")
    if assumptions.get("clan_parameters_calibrated") is not True:
        shared_missing.append("assumptions.clan_parameters_calibrated")
    boundary = snapshot.get("season_boundary_seconds")
    no_change = snapshot.get("no_season_change_before_deadline") is True
    if boundary is None and not no_change:
        shared_missing.append("season timing unknown: provide boundary or no_season_change_before_deadline=true")
    if boundary is not None:
        boundary = number(boundary, "season_boundary_seconds")
        if boundary < 0:
            raise ValueError("season_boundary_seconds must be nonnegative")
    crosses = boundary is not None and boundary < horizon
    if no_change and crosses:
        raise ValueError("no_season_change_before_deadline conflicts with season boundary")
    try:
        stocks = vector(raw_stocks, resources, "snapshot.stocks", nonnegative=True)
        floors = vector(payload.get("reserve_floors"), resources, "reserve_floors", nonnegative=True)
        if crosses or "net_rate_segments" in snapshot:
            base_parts = segments(snapshot.get("net_rate_segments"), horizon, resources,
                                  "snapshot.net_rate_segments", ["net_rates"],
                                  boundary if crosses else None)
        else:
            base_parts = [{"end": horizon, "net_rates": vector(snapshot.get("net_rates"), resources,
                                                              "snapshot.net_rates")}]
    except NeedsData as exc:
        shared_missing.append(str(exc))
    baseline = {"id": "wait", "label": "暂缓：维持当前状态", "kind": "wait", "required_workers": 0,
                "start_delay_seconds": 0, "upfront_costs": {}, "startup_costs": {},
                "startup_costs_at_seconds": 0}
    zero = {res: 0.0 for res in resources}
    results, wait_projection = [], None
    actions = payload.get("actions", [])
    ids = [action["id"] for action in actions]
    if "wait" in ids or len(ids) != len(set(ids)):
        raise ValueError("action IDs must be unique and cannot be 'wait'")
    for action in [baseline, *actions]:
        is_wait = action["id"] == "wait"
        row = {"id": action["id"], "label": action.get("label", action["id"]),
               "status": "needs_data", "missing": list(shared_missing), "risks": [],
               "delta_vs_wait": None, "production_start_seconds": None,
               "projected_stocks": None, "goal_gap": None}
        rejected = []
        if not is_wait:
            for field in ("discovered", "adjacent", "known_legal"):
                value = action.get(field)
                if value is False:
                    rejected.append(field + "=false")
                elif value is not True:
                    row["missing"].append("actions." + action["id"] + "." + field)
        try:
            workers = number(action.get("required_workers"), "required_workers")
            if workers < 0 or not workers.is_integer():
                raise ValueError("required_workers must be a nonnegative integer")
            row["required_workers"] = int(workers)
            if not is_wait:
                available = number(snapshot.get("available_workers"), "snapshot.available_workers")
                if available < 0 or not available.is_integer():
                    raise ValueError("available_workers must be a nonnegative integer")
                if workers > available:
                    rejected.append("insufficient_available_workers")
            delay = number(action.get("start_delay_seconds"), "start_delay_seconds")
            debit_time = number(action.get("startup_costs_at_seconds"), "startup_costs_at_seconds")
            if not 0 <= debit_time <= delay:
                raise ValueError("startup_costs_at_seconds must be between 0 and start delay")
            row["production_start_seconds"] = delay
            upfront = vector(action.get("upfront_costs"), resources, "upfront_costs",
                             nonnegative=True, complete=False)
            startup = vector(action.get("startup_costs"), resources, "startup_costs",
                             nonnegative=True, complete=False)
            if is_wait:
                delta_parts = [{"end": horizon, "during_setup_rate_delta": zero,
                                "after_start_rate_delta": zero}]
            elif crosses or "rate_delta_segments" in action:
                delta_parts = segments(action.get("rate_delta_segments"), horizon, resources,
                                       "rate_delta_segments",
                                       ["during_setup_rate_delta", "after_start_rate_delta"],
                                       boundary if crosses else None)
            else:
                delta_parts = [{"end": horizon,
                    "during_setup_rate_delta": vector(action.get("during_setup_rate_delta"), resources,
                                                      "during_setup_rate_delta"),
                    "after_start_rate_delta": vector(action.get("after_start_rate_delta"), resources,
                                                     "after_start_rate_delta")}]
            if rejected:
                row.update(status="rejected", rejection_reasons=rejected)
            elif not row["missing"]:
                projection = project(stocks, floors, base_parts, delta_parts, delay,
                                     [(0.0, upfront), (debit_time, startup)], horizon)
                row.update(projection)
                row["goal_gap"] = max(0.0, target - projection["projected_stocks"][goal_resource])
                row["status"] = "unsafe" if projection["reserve_violations"] else "safe"
                if projection["reserve_violations"]:
                    row["risks"].append("计划期间库存低于保留量；末库存恢复也不能消除该风险")
                if delay >= horizon and not is_wait:
                    row["risks"].append("截止前尚未投产")
                if is_wait:
                    wait_projection = projection["projected_stocks"]
                if wait_projection is not None:
                    row["delta_vs_wait"] = {res: projection["projected_stocks"][res] - wait_projection[res]
                                            for res in resources}
        except NeedsData as exc:
            row["missing"].append(str(exc))
            if rejected:
                row.update(status="rejected", rejection_reasons=rejected)
        results.append(row)
    safe = sorted((row for row in results if row["status"] == "safe"),
                  key=lambda row: (round(row["goal_gap"], 8), row["required_workers"],
                                   0 if row["id"] == "wait" else 1, row["id"]))
    for rank, row in enumerate(safe, 1):
        row["rank"] = rank
        row["ranking_reason"] = (f"截止时目标缺口 {row['goal_gap']:.6g}；缺口相同时优先较少劳力"
                                 f"（本计划 {row['required_workers']} 人）；完全并列优先暂缓")
    return {"model": "short_horizon_budget_comparator_v1", "synthetic": payload.get("synthetic", False),
            "goal": dict(goal), "recommended_action_id": safe[0]["id"] if safe else None,
            "ranked_safe_action_ids": [row["id"] for row in safe], "candidates": results,
            "limitations": ["输入参数由调用者显式提供；不自动应用氏族规则或读取游戏",
                            "线性分段预算，不模拟人口增长、战斗、路径或未知事件",
                            "available_workers 是可调配劳力输入，绝不从单位列表长度推断人口",
                            "未建模库存容量、矿藏耗尽或未来任务；输入流速必须在各分段内适用",
                            "暂缓基线假设当前活动和流速持续；不是无风险保证"]}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    try:
        result = evaluate(json.loads(args.snapshot.read_text(encoding="utf-8-sig")))
    except (ValueError, KeyError, NeedsData) as exc:
        parser.exit(2, "Invalid input: " + str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
