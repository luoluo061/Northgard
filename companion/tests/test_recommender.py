import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("recommender", ROOT / "core" / "recommender.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
evaluate = MODULE.evaluate


class BudgetComparatorTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "examples" / "food_before_winter.json").read_text(encoding="utf-8"))

    def run_model(self):
        result = evaluate(self.data)
        return result, {row["id"]: row for row in result["candidates"]}

    def test_synthetic_example_and_relative_delta(self):
        result, rows = self.run_model()
        self.assertEqual(result["recommended_action_id"], "food_zone")
        self.assertAlmostEqual(rows["food_zone"]["projected_stocks"]["Food"], 119)
        self.assertAlmostEqual(rows["food_zone"]["delta_vs_wait"]["Food"], 31)
        self.assertTrue(result["synthetic"])

    def test_insufficient_workers(self):
        self.data["snapshot"]["available_workers"] = 1
        result, rows = self.run_model()
        self.assertEqual(rows["food_zone"]["status"], "rejected")
        self.assertEqual(result["recommended_action_id"], "wait")

    def test_unknown_rate_is_not_zero(self):
        _, rows = self.run_model()
        self.assertEqual(rows["unknown_food_zone"]["status"], "needs_data")
        self.assertIsNone(rows["unknown_food_zone"]["projected_stocks"])
        self.data["actions"][0]["after_start_rate_delta"].pop("Money")
        _, rows = self.run_model()
        self.assertEqual(rows["food_zone"]["status"], "needs_data")

    def test_undiscovered_nonadjacent_and_illegal_are_rejected(self):
        for flag in ("discovered", "adjacent", "known_legal"):
            with self.subTest(flag=flag):
                self.data["actions"][0][flag] = False
                _, rows = self.run_model()
                self.assertEqual(rows["food_zone"]["status"], "rejected")
                self.data["actions"][0][flag] = True

    def test_upfront_reserve_breach_even_if_final_stock_recovers(self):
        self.data["actions"][0]["upfront_costs"]["Food"] = 70
        _, rows = self.run_model()
        row = rows["food_zone"]
        self.assertGreater(row["projected_stocks"]["Food"], 35)
        self.assertEqual(row["status"], "unsafe")
        self.assertEqual(row["reserve_violations"][0]["time_seconds"], 0)

    def test_setup_valley_checked_at_startup_debit(self):
        action = self.data["actions"][0]
        action["upfront_costs"] = {}
        action["during_setup_rate_delta"]["Food"] = -0.7
        action["startup_costs"]["Food"] = 43
        self.data["reserve_floors"]["Food"] = 50
        action["startup_costs_at_seconds"] = 10
        _, rows = self.run_model()
        row = rows["food_zone"]
        self.assertEqual(row["status"], "unsafe")
        self.assertGreater(row["projected_stocks"]["Food"], 50)
        self.assertEqual(row["reserve_violations"][0]["time_seconds"], 10)

    def test_reassignment_opportunity_cost_can_make_wait_better(self):
        self.data["actions"][0]["after_start_rate_delta"]["Food"] = -0.05
        result, rows = self.run_model()
        self.assertLess(rows["food_zone"]["delta_vs_wait"]["Food"], 0)
        self.assertEqual(result["recommended_action_id"], "wait")

    def test_when_goal_already_met_wait_uses_fewer_workers(self):
        self.data["goal"]["target"] = 80
        result, rows = self.run_model()
        self.assertEqual(rows["wait"]["goal_gap"], 0)
        self.assertEqual(rows["food_zone"]["goal_gap"], 0)
        self.assertEqual(result["recommended_action_id"], "wait")

    def test_goal_met_prefers_less_labor_instead_of_excess_production(self):
        self.data["actions"][1] = copy.deepcopy(self.data["actions"][0])
        smaller = self.data["actions"][1]
        smaller.update(id="smaller_food", required_workers=1)
        smaller["after_start_rate_delta"]["Food"] = 0.58
        result, _ = self.run_model()
        self.assertEqual(result["recommended_action_id"], "smaller_food")

    def test_crossing_season_requires_baseline_and_action_segments(self):
        self.data["snapshot"]["season_boundary_seconds"] = 60
        result, rows = self.run_model()
        self.assertIsNone(result["recommended_action_id"])
        self.assertEqual(rows["wait"]["status"], "needs_data")
        self.data["snapshot"]["net_rate_segments"] = [
            {"until_seconds": 60, "net_rates": {"Food": -0.1, "Wood": 0.2, "Money": 0.05}},
            {"until_seconds": 120, "net_rates": {"Food": -0.3, "Wood": 0.1, "Money": 0.05}},
        ]
        _, rows = self.run_model()
        self.assertEqual(rows["wait"]["status"], "safe")
        self.assertEqual(rows["food_zone"]["status"], "needs_data")
        self.data["actions"][0]["rate_delta_segments"] = [
            {"until_seconds": 60,
             "during_setup_rate_delta": {"Food": -0.1, "Wood": 0, "Money": 0},
             "after_start_rate_delta": {"Food": 0.6, "Wood": 0, "Money": 0}},
            {"until_seconds": 120,
             "during_setup_rate_delta": {"Food": -0.1, "Wood": 0, "Money": 0},
             "after_start_rate_delta": {"Food": 0.8, "Wood": 0, "Money": 0}},
        ]
        result, rows = self.run_model()
        self.assertEqual(result["recommended_action_id"], "food_zone")
        self.assertAlmostEqual(rows["food_zone"]["projected_stocks"]["Food"], 119)

    def test_null_season_timing_needs_explicit_no_change_confirmation(self):
        self.data["snapshot"]["season_boundary_seconds"] = None
        result, rows = self.run_model()
        self.assertIsNone(result["recommended_action_id"])
        self.assertEqual(rows["wait"]["status"], "needs_data")
        self.data["snapshot"]["no_season_change_before_deadline"] = True
        result, rows = self.run_model()
        self.assertEqual(result["recommended_action_id"], "food_zone")

    def test_season_segments_must_split_at_known_boundary(self):
        self.data["snapshot"]["season_boundary_seconds"] = 60
        self.data["snapshot"]["net_rate_segments"] = [
            {"until_seconds": 120, "net_rates": {"Food": -0.1, "Wood": 0.2, "Money": 0.05}},
        ]
        action = self.data["actions"][0]
        action["rate_delta_segments"] = [{
            "until_seconds": 120,
            "during_setup_rate_delta": action["during_setup_rate_delta"],
            "after_start_rate_delta": action["after_start_rate_delta"],
        }]
        result, rows = self.run_model()
        self.assertIsNone(result["recommended_action_id"])
        self.assertIn("must split", " ".join(rows["wait"]["missing"]))
        self.data["snapshot"]["net_rate_segments"].insert(0, {
            "until_seconds": 60, "net_rates": {"Food": -0.1, "Wood": 0.2, "Money": 0.05},
        })
        _, rows = self.run_model()
        self.assertEqual(rows["wait"]["status"], "safe")
        self.assertEqual(rows["food_zone"]["status"], "needs_data")
        self.assertIn("must split", " ".join(rows["food_zone"]["missing"]))

    def test_linear_reserve_crossing_reports_actual_boundary_time(self):
        self.data["snapshot"]["net_rates"]["Food"] = -1
        _, rows = self.run_model()
        violation = rows["wait"]["reserve_violations"][0]
        self.assertEqual(violation["resource"], "Food")
        self.assertAlmostEqual(violation["time_seconds"], 65)
        self.assertAlmostEqual(violation["stock"], 35)
        self.assertEqual(rows["wait"]["status"], "unsafe")

    def test_unavailable_population_or_legal_state_is_missing_data(self):
        self.data["snapshot"].pop("available_workers")
        self.data["snapshot"]["units"] = [1] * 100
        _, rows = self.run_model()
        self.assertEqual(rows["food_zone"]["status"], "needs_data")
        self.data["snapshot"]["available_workers"] = 2
        self.data["actions"][0]["known_legal"] = None
        _, rows = self.run_model()
        self.assertEqual(rows["food_zone"]["status"], "needs_data")

    def test_clan_calibration_must_be_explicit(self):
        self.data["assumptions"]["clan_parameters_calibrated"] = False
        result, rows = self.run_model()
        self.assertIsNone(result["recommended_action_id"])
        self.assertEqual(rows["food_zone"]["status"], "needs_data")

    def test_input_is_not_mutated(self):
        original = copy.deepcopy(self.data)
        evaluate(self.data)
        self.assertEqual(self.data, original)

    def test_money_cost_is_debited_without_automatic_clan_rule(self):
        self.data = json.loads((ROOT / "examples" / "raven_money_colonization.json").read_text(encoding="utf-8"))
        result, rows = self.run_model()
        row = rows["synthetic_money_cost_zone"]
        self.assertEqual(result["recommended_action_id"], "synthetic_money_cost_zone")
        self.assertAlmostEqual(row["delta_vs_wait"]["Money"], -15)
        self.assertAlmostEqual(row["projected_stocks"]["Food"], 116.5)
        self.data["assumptions"]["clan"] = "Bear"
        changed, changed_rows = self.run_model()
        self.assertEqual(changed_rows["synthetic_money_cost_zone"]["projected_stocks"], row["projected_stocks"])

    def test_startup_cost_time_is_required_and_validated(self):
        self.data["actions"][0].pop("startup_costs_at_seconds")
        _, rows = self.run_model()
        self.assertEqual(rows["food_zone"]["status"], "needs_data")
        self.data["actions"][0]["startup_costs_at_seconds"] = 100
        with self.assertRaises(ValueError):
            self.run_model()


if __name__ == "__main__":
    unittest.main()
