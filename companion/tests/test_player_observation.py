import copy
import importlib.util
import json
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location("player_observation", Path(__file__).resolve().parents[1] / "core/player_observation.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def snapshot(**extra):
    data = {"schema_version": 2, "status": "ok", "session_key": "match-A",
            "local_player_entity_uid": 31,
            "local_player_identity": {"verified": True, "role": "player", "entity_uid": 31,
                                      "is_ai": False, "in_player_list": True},
            "game_time_seconds": 120.5, "clan": "Bear", "captured_at": "2026-10-23T10:00:00+08:00",
            "stocks": {key: 100.25 for key in MODULE.CORE_RESOURCES},
            "population": 4, "population_cap": 6, "in_winter": False,
            "owned_zone_ids": [87], "discovered_zone_ids": [87, 88],
            "discovered_tiles": [{"id": 87, "owner": "self", "base_building_slots": 4,
                                  "buildings": ["FAKE_DO_NOT_FORWARD"]},
                                 {"id": 88, "owner": "other", "buildings": ["secret_enemy_upgrade"]}],
            "units": [{"uid": 10, "kind": "Villager", "zone_id": 87,
                       "assigned_job": None, "cur_job": {"kind": "Active", "args": []},
                       "produce": "Food", "has_no_work": False, "position": {"x": 1, "y": 2}}],
            "buildings": [{"uid": 20, "kind": "TownHall", "zone_id": 87}],
            "native_events": []}
    data.update(extra)
    return data


def actor(uid=31, **extra):
    return {"entity_type": "ent.Player", "entity_uid": uid, "clan": "Bear", **extra}


def event(kind="Colonize", params=None, **extra):
    return {"index": 9, "time": 120, "kind": kind,
            "params": params if params is not None else [actor(), {"entity_type": "ent.Zone", "zone_id": 88}], **extra}


def snapshot_with_economy():
    source = snapshot(capture_run_id="run-A", capture_segment_id="run-A:0", game_build="25096976")
    economy = {"schema_version": 1, "status": "ok", "source": "native_economic_caches_read_only",
               "hlboot_sha256": MODULE._ECONOMY_BUILD_HASH, "resource_table_fingerprint": "a" * 64,
               "game_build": "25096976", "game_time_seconds": 120.5, "time_before": 120.5,
               "time_after": 120.5, "sample_span_game_seconds": 0, "read_guard_issues": [],
               "session_key": "match-A", "capture_run_id": "run-A", "capture_segment_id": "run-A:0",
               "resources": {}}
    for name, native in (("Food", "currentFood"), ("Wood", "currentWood"), ("Money", "currentMoney")):
        economy["resources"][name] = {"stock": 100.25, "cached_at_game_time": 120,
            "age_game_seconds": .5, "raw_cached_production_per_game_second": .5,
            "consumption_per_game_second": .2, "net_per_game_second": .7,
            "gross_aggregate_per_game_second": .9, "total_produced_counter": 50,
            "stock_cap": None, "native_net_field": native,
            "gross_aggregate_source": "native_current_net_plus_native_consumption",
            "food_base_cache_is_total": False if name == "Food" else None}
    source["economy"] = economy
    return source


def bootstrap_snapshot():
    source = snapshot(game_time_seconds=0., population_cap=0,
                      capture_run_id='run-A', capture_segment_id='run-A:0')
    source['game_context'] = {'mode': {'kind': 'Singleplayer', 'args': [
        {'kind': 'FreeForAll', 'args': []}]}, 'editor_present': False,
        'bifrost_data_present': False}
    source['buildings'][0]['is_construction'] = False
    source['economy'] = {'schema_version': 1, 'status': 'not_ready',
        'source': 'native_economic_caches_read_only', 'hlboot_sha256': MODULE._ECONOMY_BUILD_HASH,
        'reason_codes': ['economic_cache_not_initialized'],
        **{key: source[key] for key in ('session_key', 'capture_run_id', 'capture_segment_id')}}
    return source


class PlayerObservationTests(unittest.TestCase):
    def test_explicit_opening_cache_not_ready_withholds_cap_without_mutating_native_zero(self):
        source = bootstrap_snapshot()
        before = copy.deepcopy(source)
        result = MODULE.project_player_observation(source)
        self.assertIsNone(result['population_cap'])
        self.assertEqual(result['unknown_fields']['population_cap'], 'bootstrap_cache_not_initialized')
        self.assertEqual(result['economy'], {'schema_version': 1, 'status': 'not_ready',
            'scope': 'no_economic_data', 'reason_codes': ['economic_cache_not_initialized']})
        self.assertEqual(source, before)
        self.assertEqual(source['population_cap'], 0)

    def test_not_ready_does_not_forward_extra_raw_payload(self):
        source = bootstrap_snapshot()
        source['economy'].update(resources={'Food': {'stock': 999, 'name': 'PRIVATE_PAYLOAD'}},
                                  raw_pointer='PRIVATE_POINTER', error='PRIVATE_ERROR')
        result = MODULE.project_player_observation(source)
        self.assertEqual(set(result['economy']), {'schema_version', 'status', 'scope', 'reason_codes'})
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_unverified_not_ready_evidence_rejected_and_zero_cap_preserved(self):
        for key, value in (('schema_version', True), ('source', 'unverified'),
                           ('hlboot_sha256', 'bad'), ('reason_codes', []),
                           ('session_key', 'other'), ('capture_run_id', 'other'),
                           ('capture_segment_id', 'other')):
            with self.subTest(field=key):
                source = bootstrap_snapshot()
                source['economy'][key] = value
                result = MODULE.project_player_observation(source)
                self.assertEqual(result['economy']['status'], 'rejected')
                self.assertEqual(result['population_cap'], 0)
                self.assertNotIn('population_cap', result['unknown_fields'])

    def test_later_real_zero_cap_and_ready_opening_zero_cap_are_preserved(self):
        later = bootstrap_snapshot()
        later['game_time_seconds'] = 300.
        self.assertEqual(MODULE.project_player_observation(later)['population_cap'], 0)
        ready = snapshot_with_economy()
        ready.update(game_time_seconds=0., population_cap=0)
        ready['game_context'] = bootstrap_snapshot()['game_context']
        ready['buildings'][0]['is_construction'] = False
        ready['economy'].update(game_time_seconds=0., time_before=0., time_after=0.)
        for resource in ready['economy']['resources'].values():
            resource.update(cached_at_game_time=0., age_game_seconds=0.)
        result = MODULE.project_player_observation(ready)
        self.assertEqual(result['economy']['status'], 'ok')
        self.assertEqual(result['population_cap'], 0)

    def test_missing_error_or_unsupported_economy_is_not_initialization_evidence(self):
        for economy in (None, {}, {'status': 'read_error'}, {'status': 'unsupported_resource_mode'}):
            with self.subTest(economy=economy):
                source = bootstrap_snapshot()
                source['economy'] = economy
                self.assertEqual(MODULE.project_player_observation(source)['population_cap'], 0)

    def test_bootstrap_cap_requires_audited_mode_positive_population_and_completed_own_hall(self):
        for scenario in ('unsupported_clan', 'unknown_mode', 'unowned_hall', 'construction', 'zero_population'):
            with self.subTest(scenario=scenario):
                source = bootstrap_snapshot()
                if scenario == 'unsupported_clan':
                    source['clan'] = 'Rat'
                elif scenario == 'unknown_mode':
                    del source['game_context']
                elif scenario == 'unowned_hall':
                    source['buildings'][0]['kind'] = 'House'
                elif scenario == 'construction':
                    source['buildings'][0]['is_construction'] = True
                else:
                    source['population'] = 0
                self.assertEqual(MODULE.project_player_observation(source)['population_cap'], 0)

    def test_own_state_is_projected_without_mutating_source(self):
        source = snapshot()
        before = copy.deepcopy(source)
        output = MODULE.project_player_observation(source)
        self.assertEqual(output["status"], "ok")
        self.assertEqual(output["units"][0]["cur_job"], {"kind": "Active", "args": []})
        self.assertEqual(output["tiles"][0]["buildings"], ["TownHall"])
        self.assertEqual(source, before)
        output["stocks"]["Food"] = 0
        self.assertEqual(source["stocks"]["Food"], 100.25)

    def test_past_discovery_never_unlocks_current_enemy_objects(self):
        source = snapshot(visible_zone_ids=[88], visibility_verified=True)
        output = MODULE.project_player_observation(source)
        tile = next(t for t in output["tiles"] if t["id"] == 88)
        self.assertEqual(tile, {"id": 88, "knowledge_scope": "discovered_id_only", "current_visibility": "unverified"})
        self.assertNotIn("secret_enemy_upgrade", json.dumps(output))

    def test_missing_identity_and_spectators_rejected(self):
        variants = [None, {}, {"verified": True, "role": "spectator", "entity_uid": 31,
                              "is_ai": False, "in_player_list": True},
                    {"verified": True, "role": "player", "entity_uid": 32,
                     "is_ai": False, "in_player_list": True},
                    {"verified": True, "role": "player", "entity_uid": 31,
                     "is_ai": True, "in_player_list": True}]
        for identity in variants:
            with self.subTest(identity=identity):
                self.assertEqual(MODULE.project_player_observation(snapshot(local_player_identity=identity))["status"], "rejected")
        self.assertEqual(MODULE.project_player_observation(snapshot(is_spectator=True))["status"], "rejected")

    def test_missing_session_and_empty_ok_frame_rejected(self):
        for changes in ({"session_key": None}, {"units": None}, {"stocks": {}},
                        {"units": [], "buildings": [], "owned_zone_ids": []},
                        {"schema_version": 1}):
            with self.subTest(changes=changes):
                result = MODULE.project_player_observation(snapshot(**changes))
                self.assertEqual(result["status"], "rejected")
                self.assertNotIn("stocks", result)

    def test_default_drops_entire_native_event_stream(self):
        output = MODULE.project_player_observation(snapshot(native_events=[event()]))
        self.assertEqual(output["own_events"], [])
        self.assertEqual(output["quality"]["native_events_policy"], "disabled")

    def test_own_success_event_allowed_and_enemy_and_globals_rejected(self):
        events = [event(), event(params=[actor(55), {"entity_type": "ent.Zone", "zone_id": 88}]),
                  event("SetWinter", [True]), event("RemoveBuilding", [{"entity_type": "ent.Zone", "zone_id": 87}, "House", {}]),
                  event("LearnTech", [actor(), "WinterFestival", 1]), event("Feast", [actor()])]
        result = MODULE.project_player_observation(snapshot(native_events=events), include_own_events=True)
        self.assertEqual([row["kind"] for row in result["own_events"]], ["Colonize", "LearnTech", "Feast"])
        self.assertEqual(result["own_events"][0]["actor"], "self")
        self.assertNotIn("params", result["own_events"][0])

    def test_own_actor_does_not_unlock_foreign_nested_owner_or_enum(self):
        nested = {"kind": "Owner", "args": [actor(55)]}
        events = [event(params=[actor(owner=actor(55)), {"entity_type": "ent.Zone", "zone_id": 88}]),
                  event(params=[actor(), {"entity_type": "ent.Zone", "zone_id": 88, "owner": nested}]),
                  event("LearnTech", [actor(), nested, 1]),
                  event("UnderAttack", [actor(), {"entity_type": "ent.Zone", "zone_id": 87}, actor(55)]),
                  event("AddBuilding", [actor(), {"entity_type": "ent.Zone", "zone_id": 87}, "House",
                                         {"x": 1, "y": 2, "width": 3, "height": 4, "owner": nested}])]
        result = MODULE.project_player_observation(snapshot(native_events=events), include_own_events=True)
        self.assertEqual(result["own_events"], [])

    def test_sensitive_fields_and_raw_maps_never_pass_through(self):
        source = snapshot(pid=9000, account="SECRET_ACCOUNT", name="SECRET_NAME",
                          players=[{"name": "SECRET_ENEMY"}], ai_goals={"secret_goal": 1},
                          raw_resource_map={"SECRET_RESOURCE": 123}, pointer="0x12345678")
        source["stocks"]["SECRET_RESOURCE"] = 22
        source["units"][0].update(name="SECRET_UNIT", owner_name="SECRET_OWNER", raw_pointer=12345)
        source["buildings"][0]["state"] = {"contains": {"name": "SECRET_BUILDING"}}
        text = json.dumps(MODULE.project_player_observation(source))
        self.assertNotIn("SECRET", text)
        for name in ("ai_goals", "raw_resource_map", "players", "pointer", '"pid"'):
            self.assertNotIn(name, text)

    def test_enemy_target_and_job_args_omitted(self):
        source = snapshot()
        source["units"][0].update(going_to={"entity_type": "ent.Unit", "uid": 999, "kind": "SECRET_ENEMY"},
                                  assigned_job={"kind": "Attack", "args": [actor(55), {"secret": "SECRET_JOB"}]})
        result = MODULE.project_player_observation(source)
        self.assertIsNone(result["units"][0]["going_to"])
        self.assertEqual(result["units"][0]["assigned_job"], {"kind": "Attack", "args_status": "unverified_arguments_omitted"})
        self.assertNotIn("SECRET", json.dumps(result))

    def test_own_reference_uses_canonical_fields(self):
        source = snapshot()
        source["units"][0]["assign_to"] = {"entity_type": "ent.Building", "uid": 20, "kind": "SECRET_INJECTED", "name": "SECRET_NAME"}
        result = MODULE.project_player_observation(source)
        self.assertEqual(result["units"][0]["assign_to"]["kind"], "TownHall")
        self.assertNotIn("SECRET", json.dumps(result))

    def test_foreign_entity_inside_local_collection_rejected(self):
        source = snapshot()
        source["units"][0]["owner_uid"] = 55
        self.assertEqual(MODULE.project_player_observation(source)["status"], "rejected")
        source = snapshot()
        source["buildings"][0]["zone_id"] = 88
        self.assertEqual(MODULE.project_player_observation(source)["status"], "rejected")

    def test_own_scout_entering_unfinished_exploration_does_not_drop_frame(self):
        source = snapshot()
        source["units"][0].update(kind="Scout", zone_id=99, target_zone_id=99,
                                  going_to={"entity_type": "ent.Zone", "zone_id": 99})
        result = MODULE.project_player_observation(source)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["units"][0]["kind"], "Scout")
        self.assertEqual(result["units"][0]["uid"], 10)
        self.assertIsNone(result["units"][0]["zone_id"])
        self.assertIsNone(result["units"][0]["target_zone_id"])
        self.assertIsNone(result["units"][0]["going_to"])
        self.assertIn("units.zone_id", result["unknown_fields"])
        self.assertEqual(result["discovered_zone_ids"], [87, 88])
        source["units"][0]["owner_uid"] = 55
        self.assertEqual(MODULE.project_player_observation(source)["status"], "rejected")

    def test_recursive_reference_does_not_escape_or_crash(self):
        source = snapshot()
        recursive = {"kind": "Attack"}
        recursive["args"] = [recursive]
        source["units"][0]["assigned_job"] = recursive
        recursive_event = event()
        recursive_event["params"][0]["owner"] = recursive_event
        source["native_events"] = [recursive_event]
        source["unused_raw"] = source
        result = MODULE.project_player_observation(source, include_own_events=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["own_events"], [])
        json.dumps(result, allow_nan=False)

    def test_nonfinite_and_duplicate_entities_rejected(self):
        source = snapshot()
        source["stocks"]["Food"] = float("nan")
        self.assertEqual(MODULE.project_player_observation(source)["status"], "rejected")
        source = snapshot()
        source["units"].append(copy.deepcopy(source["units"][0]))
        self.assertEqual(MODULE.project_player_observation(source)["status"], "rejected")

    def test_economy_uses_three_identity_keys_and_explicit_pending_calibration(self):
        source = snapshot_with_economy()
        result = MODULE.project_player_observation(source)
        projected = result["economy"]
        self.assertEqual(projected["status"], "ok")
        self.assertEqual(projected["capture_segment_id"], "run-A:0")
        self.assertEqual(result["capture_segment_id"], "run-A:0")
        self.assertEqual(projected["resources"]["Food"]["gross_aggregate_per_game_second"], .9)
        self.assertFalse(projected["resources"]["Food"]["food_base_cache_is_total"])
        self.assertFalse(projected["quality"]["live_calibration_complete"])
        self.assertEqual(projected["rate_unit"], "resource_per_game_second")
        self.assertIn("total_produced_semantics", projected)

    def test_economy_cross_session_run_or_segment_rejected(self):
        for field in ("session_key", "capture_run_id", "capture_segment_id"):
            for value in (None, "other-match"):
                with self.subTest(field=field, value=value):
                    source = snapshot_with_economy()
                    source["economy"][field] = value
                    result = MODULE.project_player_observation(source)
                    self.assertEqual(result["status"], "ok")
                    self.assertEqual(result["economy"]["status"], "rejected")
                    self.assertNotIn("resources", result["economy"])

    def test_economy_drops_raw_slots_maps_and_unknown_nested_values(self):
        source = snapshot_with_economy()
        source["economy"]["raw_resource_slots"] = [{"name": "SECRET_SLOT", "pointer": "0x12345678"}]
        source["economy"]["total_produced_counters"] = {"SECRET_COUNTER": 99}
        source["economy"]["resources"]["SECRET_RESOURCE"] = {"stock": 1000}
        source["economy"]["resources"]["Food"]["name"] = "SECRET_ACCOUNT"
        source["economy"]["resources"]["Wood"]["consumption_per_game_second"] = {"owner": "SECRET_OWNER"}
        source["economy"]["read_guard_issues"] = ["SECRET_GUARD"]
        result = MODULE.project_player_observation(source)
        self.assertIsNone(result["economy"]["resources"]["Wood"]["consumption_per_game_second"])
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertNotIn("raw_resource_slots", result["economy"])
        self.assertNotIn("total_produced_counters", result["economy"])

    def test_economy_read_race_and_cache_timestamps_remain_visible(self):
        source = snapshot_with_economy()
        source["economy"].update(status="read_race", read_guard_issues=["cache_refreshed_during_read"])
        result = MODULE.project_player_observation(source)
        self.assertEqual(result["economy"]["status"], "read_race")
        self.assertEqual(result["economy"]["read_guard_issues"], ["cache_refreshed_during_read"])
        self.assertEqual(result["economy"]["resources"]["Food"]["cached_at_game_time"], 120)
        self.assertEqual(result["economy"]["resources"]["Food"]["age_game_seconds"], .5)
        self.assertIn("economy.consistency", result["unknown_fields"])

    def test_economy_unsupported_build_or_nonfinite_stock_rejected(self):
        for mutation in (lambda e: e.update(hlboot_sha256="b" * 64),
                         lambda e: e.update(game_build="different"),
                         lambda e: e["resources"]["Food"].update(stock=float("nan"))):
            source = snapshot_with_economy()
            mutation(source["economy"])
            result = MODULE.project_player_observation(source)
            self.assertEqual(result["economy"]["status"], "rejected")
            self.assertNotIn("resources", result["economy"])

    def test_economy_recursive_unknown_value_is_omitted(self):
        source = snapshot_with_economy()
        recursive = {}
        recursive["owner"] = recursive
        source["economy"]["resources"]["Food"]["gross_aggregate_source"] = recursive
        source["economy"]["raw_resource_slots"] = source
        result = MODULE.project_player_observation(source)
        self.assertIsNone(result["economy"]["resources"]["Food"]["gross_aggregate_source"])
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
