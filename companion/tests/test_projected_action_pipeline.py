"""Exercise the production projector -> differ boundary, not mock event plumbing."""
import copy
import importlib.util
from pathlib import Path
import unittest

CORE = Path(__file__).resolve().parents[1] / "core"


def load(name):
    spec = importlib.util.spec_from_file_location(name, CORE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROJECT = load("player_observation")
DIFF = load("action_diff")


def raw(t=120.5, events=(), **extra):
    result = {"schema_version": 2, "status": "ok", "session_key": "match-A",
        "capture_run_id": "run-A", "capture_segment_id": "run-A:1",
        "local_player_entity_uid": 31,
        "local_player_identity": {"verified": True, "role": "player", "entity_uid": 31,
                                  "is_ai": False, "in_player_list": True},
        "game_time_seconds": t, "clan": "Bear", "stocks": {key:100 for key in PROJECT.CORE_RESOURCES},
        "population": 4, "population_cap": 6, "in_winter": False,
        "owned_zone_ids": [87], "discovered_zone_ids": [87,88],
        "units": [{"uid":10,"kind":"Villager","zone_id":87}],
        "buildings": [{"uid":20,"kind":"TownHall","zone_id":87}],
        "native_events": list(events)}
    result.update(extra)
    return result


def native(kind="Colonize", time=121, index=9, actor=31, zone=88, extra=None):
    params = [{"entity_type":"ent.Player","entity_uid":actor,"clan":"Bear"}]
    if kind in ("Colonize","Discover","AddBuilding"):
        params.append({"entity_type":"ent.Zone","zone_id":zone})
    if kind == "AddBuilding":
        params += ["House", {"x":10,"y":20,"width":0,"height":0}]
    elif kind == "LearnTech":
        params += ["WinterFestival", 1]
    result = {"index":index,"time":time,"kind":kind,"params":params}
    if extra:
        result.update(extra)
    return result


def project(source):
    result = PROJECT.project_player_observation(source,include_own_events=True)
    if result["status"] != "ok":
        raise AssertionError(result)
    return result


class ProjectedActionPipelineTests(unittest.TestCase):
    def test_real_projected_events_reach_differ_once_and_retain_identity(self):
        old = native(time=100,index=1)
        first = project(raw(events=[old]))
        after = project(raw(121.5,events=[old,native()]))
        before_copy = copy.deepcopy(after)
        differ = DIFF.ActionDiffer()
        self.assertEqual(differ.feed(first)["native_events_already_present"],1)
        result = differ.feed(after)
        self.assertEqual(len(result["new_native_events"]),1)
        event = result["new_native_events"][0]
        self.assertEqual((event["kind"],event["zone_id"]),("Colonize",88))
        self.assertEqual(event["local_player_entity_uid"],31)
        for field in ("session_key","capture_run_id","capture_segment_id"):
            self.assertEqual(event[field],after[field])
        self.assertEqual(event["params_schema"],"sanitized_own_success_projection.v1")
        repeated = differ.feed({**after,"game_time_seconds":122})
        self.assertEqual(repeated["new_native_events"],[])
        self.assertEqual(after,before_copy)

    def test_completion_uses_known_zone_kind_without_inventing_position(self):
        construction = {"uid":21,"kind":"Construction","is_construction":True,
                        "target_kind":"House","zone_id":87,"position":{"x":10,"y":20}}
        first = project(raw(buildings=[{"uid":20,"kind":"TownHall","zone_id":87},construction]))
        last = project(raw(121.5,events=[native("AddBuilding",zone=87)]))
        differ = DIFF.ActionDiffer();differ.feed(first)
        result = differ.feed(last)
        self.assertEqual([x["kind"] for x in result["events"]],["construction_completed"])
        self.assertIsNone(result["new_native_events"][0]["params"][3])
        self.assertNotIn("position",last["own_events"][0])

    def test_two_ambiguous_constructions_are_not_completed_by_one_event(self):
        construction = {"uid":21,"kind":"Construction","is_construction":True,
                        "target_kind":"House","zone_id":87}
        first = project(raw(buildings=[{"uid":20,"kind":"TownHall","zone_id":87},
                                       construction,{**construction,"uid":22}]))
        last = project(raw(121.5,events=[native("AddBuilding",zone=87)]))
        differ=DIFF.ActionDiffer();differ.feed(first)
        self.assertEqual([x["kind"] for x in differ.feed(last)["events"]],
                         ["construction_removed_unresolved","construction_removed_unresolved"])

    def test_foreign_global_unknown_and_nested_event_payloads_stay_out(self):
        foreign = native(actor=55)
        unknown_zone = native(zone=999)
        nested = native();nested["params"][1]["owner"]={"name":"SECRET"}
        global_event={"index":10,"time":121,"kind":"SetWinter","params":[True]}
        last=project(raw(121.5,events=[native(),foreign,unknown_zone,nested,global_event]))
        # Even an accidentally supplied unsanitized native_events field is
        # ignored in favor of the verified projection inside a PlayerObservation.
        last["native_events"]=[foreign,global_event]
        differ=DIFF.ActionDiffer();differ.feed(project(raw()))
        result=differ.feed(last)
        self.assertEqual([x["kind"] for x in result["new_native_events"]],["Colonize"])
        self.assertNotIn("SECRET",str(result))
        self.assertNotIn("55",str(result["new_native_events"]))

    def test_forged_projected_payload_and_mismatched_context_are_rejected(self):
        base=project(raw(121.5,events=[native()]))
        for updates in ({"params":[{"entity_uid":55}]},{"name":"SECRET"},
                        {"capture_segment_id":"run-B:1"},{"local_player_entity_uid":55},
                        {"zone_id":999},{"kind":["Colonize"]},{"actor":"other"}):
            with self.subTest(updates=updates):
                changed=copy.deepcopy(base);changed["own_events"][0].update(updates)
                self.assertEqual(PROJECT.action_diff_observation(changed)["native_events"],[])

    def test_unsupported_policy_or_missing_recording_identity_cannot_unlock_events(self):
        base=project(raw(121.5,events=[native()]))
        disabled=copy.deepcopy(base);disabled["quality"]["native_events_policy"]="disabled"
        self.assertEqual(PROJECT.action_diff_observation(disabled)["native_events"],[])
        for field in ("capture_run_id","capture_segment_id","session_key"):
            changed=copy.deepcopy(base);changed.pop(field)
            with self.subTest(field=field),self.assertRaises(ValueError):
                PROJECT.action_diff_observation(changed)

    def test_projected_history_payload_mutation_starts_a_new_segment(self):
        first=project(raw(events=[native("LearnTech",time=100)]))
        after=copy.deepcopy(first);after["game_time_seconds"]=121.5
        after["own_events"][0]["tech_id"]="Lumber"
        differ=DIFF.ActionDiffer();differ.feed(first)
        result=differ.feed(after)
        self.assertEqual(result["reason"],"native_log_replaced_or_truncated")
        self.assertEqual(result["record_kind"],"baseline")

    def test_new_session_does_not_generate_cross_match_events(self):
        first=project(raw())
        last=project(raw(121.5,events=[native()],session_key="match-B"))
        differ=DIFF.ActionDiffer();differ.feed(first)
        result=differ.feed(last)
        self.assertEqual(result["reason"],"session_identity_changed")
        self.assertNotIn("new_native_events",result)

    def test_external_raw_events_cannot_bypass_projected_boundary(self):
        source=project(raw())
        differ=DIFF.ActionDiffer()
        with self.assertRaises(ValueError):
            differ.feed(source,native_events=[native()])


if __name__ == "__main__":
    unittest.main()
