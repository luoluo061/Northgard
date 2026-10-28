"""Fail-closed player observation projection from a research ActionProbe row.

This module is deliberately independent of process-memory readers.  It is a
field whitelist, not a visibility detector. Own entities remain separately
scoped; the optional known-zone component permits only its audited natural
resource and unowned Wolf/Bear contract, not arbitrary outside-owned entities.
The live collector must verify local-player identity before calling it.  A
research_only label does not grant permission to expose a research field.
"""
from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
import re
import sys
from typing import Any

try:
    from .operation_projection import project_operations
    from .map_projection import project_map
    from .labor_projection import project_labor
    from .growth_projection import (project_growth, economic_cache_not_ready,
                                    population_cap_bootstrap_unavailable)
    from .match_context_projection import project_match_context
    from .known_zone_details import project_known_zone_details
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from operation_projection import project_operations
    from map_projection import project_map
    from labor_projection import project_labor
    from growth_projection import (project_growth, economic_cache_not_ready,
                                   population_cap_bootstrap_unavailable)
    from match_context_projection import project_match_context
    from known_zone_details import project_known_zone_details


POLICY_VERSION = "player-observation.v1"
CORE_RESOURCES = ("Food", "Wood", "Money", "Stone", "Iron", "Lore")
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,95}\Z")
_SESSION = re.compile(r"[A-Za-z0-9_:-]{1,128}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_ECONOMY_BUILD_HASH = "db0459d251879698420f58f6819e0ab2f75a3cde6e65611f2ad723f3b4b7ef76"
_ECONOMY_DATA_HASH = "88d4c84b9622ce237533afc8674174aa9006d87a03258cd7e5fd671f930174e3"
_ECONOMY_RESOURCES = ("Food", "Wood", "Money")
_CAPTURE_IDENTITIES = ("session_key", "capture_run_id", "capture_segment_id")
_NO = object()


def _integer(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value < 2 ** 53


def _number(value: Any) -> bool:
    if type(value) is int:
        return -(2 ** 53) < value < 2 ** 53
    return type(value) is float and math.isfinite(value)


def _token(value: Any) -> bool:
    return type(value) is str and _TOKEN.fullmatch(value) is not None


def _ids(value: Any) -> bool:
    return (type(value) is list and len(value) <= 10000
            and all(_integer(item) for item in value)
            and len(value) == len(set(value)))


def _reject(reason: str) -> dict:
    # Never echo source data or exception text in diagnostics.
    return {"schema_version": 1, "observation_type": "PlayerObservation",
            "policy_version": POLICY_VERSION, "status": "rejected",
            "scope": "no_player_data", "reason_codes": [reason],
            "unknown_fields": {"player_observation": "unavailable"}}


def _owner_matches(record: dict, own_uid: int) -> bool:
    """Optional source ownership annotations may restrict, never expand, scope."""
    for key in ("owner_uid", "owner_entity_uid", "player_uid", "player_entity_uid"):
        if key in record and record[key] != own_uid:
            return False
    if "owner" in record:
        owner = record["owner"]
        if owner != "self" and not (_integer(owner, 1) and owner == own_uid):
            return False
    return True


def _position(value: Any) -> dict | None:
    if (type(value) is dict and _number(value.get("x"))
            and _number(value.get("y"))):
        return {"x": value["x"], "y": value["y"]}
    return None


def _entity_reference(value: Any, own_entities: dict, discovered: set,
                      unknown: dict, field: str) -> dict | None:
    if value is None:
        return None
    if type(value) is not dict:
        unknown[field] = "unsupported_reference_omitted"
        return None
    uid = value.get("uid")
    # Only canonical own-entity attributes are used; reference attributes such
    # as kind, owner, arbitrary args or account names are never copied.
    if _integer(uid) and uid in own_entities:
        canonical = own_entities[uid]
        return {"uid": uid, "entity_scope": canonical["entity_scope"],
                "kind": canonical["kind"], "zone_id": canonical["zone_id"]}
    zid = value.get("zone_id")
    if value.get("entity_type") == "ent.Zone" and _integer(zid) and zid in discovered:
        return {"entity_scope": "discovered_zone_id_only", "zone_id": zid}
    unknown[field] = "non_owned_or_unverified_reference_omitted"
    return None


def _job(value: Any, own_entities: dict, discovered: set, unknown: dict,
         field: str) -> dict | None:
    if value is None:
        return None
    if type(value) is not dict or not _token(value.get("kind")):
        unknown[field] = "unsupported_job_omitted"
        return None
    # Argument semantics are not generally verified. Keep the task label and
    # explicitly omit all arguments rather than recursively serializing an
    # enum that may contain an enemy, owner object, pointer or recursive ref.
    result = {"kind": value["kind"]}
    args = value.get("args")
    if type(args) is not list or args:
        result["args_status"] = "unverified_arguments_omitted"
        unknown[field + ".args"] = "unverified_arguments_omitted"
    else:
        result["args"] = []
    return result


def _own_actor(value: Any, own_uid: int) -> bool:
    # An exact actor schema rejects a nested owner/enum even when its outer UID
    # claims to be local. Clan is accepted as a source annotation but omitted.
    return (type(value) is dict
            and set(value) <= {"entity_type", "entity_uid", "clan"}
            and value.get("entity_type") in ("ent.Player", "ent.Faction")
            and _integer(value.get("entity_uid"), 1)
            and value["entity_uid"] == own_uid
            and ("clan" not in value or _token(value["clan"])))


def _event_zone(value: Any, discovered: set) -> int | object:
    if (type(value) is dict and set(value) == {"entity_type", "zone_id"}
            and value.get("entity_type") == "ent.Zone"
            and _integer(value.get("zone_id")) and value["zone_id"] in discovered):
        return value["zone_id"]
    return _NO


def _own_event(event: Any, own_uid: int, discovered: set,
               current_time: float) -> dict | None:
    """Whitelist verified actor-first event schemas; no arbitrary params out."""
    if type(event) is not dict or not _integer(event.get("index")):
        return None
    when, kind, params = event.get("time"), event.get("kind"), event.get("params")
    if (not _number(when) or not 0 <= when <= current_time + 1
            or type(params) is not list or not params
            or not _own_actor(params[0], own_uid)):
        return None
    result = {"index": event["index"], "time": when, "kind": kind,
              "actor": "self", "source": "native_own_success_event"}
    if kind in ("Feast", "Defeat", "Victory", "Overwork", "Ignite"):
        return result if len(params) == 1 else None
    if kind in ("Discover", "Colonize", "Uncolonize", "Explore", "AddBuilding"):
        expected = {"Discover": 2, "Colonize": 2, "Uncolonize": 3,
                    "Explore": 3, "AddBuilding": 4}[kind]
        if len(params) != expected:
            return None
        zid = _event_zone(params[1], discovered)
        if zid is _NO:
            return None
        result["zone_id"] = zid
        if kind == "Uncolonize":
            if type(params[2]) is not bool:
                return None
            result["native_flag"] = params[2]
        if kind in ("Explore", "AddBuilding"):
            if not _token(params[2]):
                return None
            result["building_kind"] = params[2]
        if kind == "AddBuilding":
            rect = params[3]
            if (type(rect) is not dict or set(rect) != {"x", "y", "width", "height"}
                    or not all(_number(v) for v in rect.values())):
                return None
            # Geometric data is not needed to show the known completion fact.
        return result
    if kind in ("LearnTech", "LearnFreeTech", "HireWarchief", "UpgradeUnit"):
        if len(params) != (3 if kind == "LearnTech" else 2) or not _token(params[1]):
            return None
        if kind == "LearnTech" and not _integer(params[2]):
            return None
        key = "tech_id" if kind.startswith("Learn") else "unit_or_upgrade_kind"
        result[key] = params[1]
        return result
    # In particular: no globals, no RemoveBuilding without an actor, no attack
    # or transfer event carrying another faction, and no unverified enums.
    return None


def _unlimited_capacity_proof(proof: Any, resource: str, snapshot: dict) -> bool:
    context = snapshot.get('game_context')
    return (resource in ('Wood', 'Money', 'Stone', 'Iron', 'Lore')
            and type(proof) is dict and proof.get('resource') == resource
            and proof.get('stock_cap') is None and proof.get('stock_cap_verified') is True
            and proof.get('stock_cap_source') == 'verified_native_no_finite_limit'
            and proof.get('stock_cap_status') == 'no_finite_limit_verified'
            and proof.get('hlboot_sha256') == _ECONOMY_BUILD_HASH
            and proof.get('data_cdb_sha256') == _ECONOMY_DATA_HASH
            and snapshot.get('clan') in ('Bear', 'Raven') and type(context) is dict
            and context.get('mode') == {'kind': 'Singleplayer', 'args': [{'kind': 'FreeForAll', 'args': []}]}
            and context.get('bifrost_data_present') is False and context.get('editor_present') is False)


def _project_economy(value: Any, snapshot: dict, unknown: dict) -> dict:
    """Accept the versioned local cache contract, never its arbitrary raw maps."""
    def blocked(reason):
        unknown["economy"] = reason
        return {"schema_version": 1, "status": "rejected", "reason_codes": [reason],
                "scope": "no_economic_data"}

    if type(value) is dict and value.get('status') == 'not_ready':
        if not economic_cache_not_ready(value, snapshot):
            return blocked('economic_not_ready_evidence_invalid')
        unknown['economy'] = 'economic_cache_not_initialized'
        return {'schema_version': 1, 'status': 'not_ready', 'scope': 'no_economic_data',
                'reason_codes': ['economic_cache_not_initialized']}

    if (type(value) is not dict or value.get("schema_version") != 1
            or value.get("status") not in ("ok", "read_race")
            or value.get("source") != "native_economic_caches_read_only"):
        return blocked("unsupported_economic_component")
    for key in _CAPTURE_IDENTITIES:
        expected = snapshot.get(key)
        if (type(expected) is not str or _SESSION.fullmatch(expected) is None
                or value.get(key) != expected):
            return blocked("economic_identity_missing_or_mismatched")
    fingerprint = value.get("resource_table_fingerprint")
    if (value.get("hlboot_sha256") != _ECONOMY_BUILD_HASH
            or type(fingerprint) is not str or _SHA256.fullmatch(fingerprint) is None):
        return blocked("economic_build_or_table_unverified")
    if (value.get("game_build") is not None and snapshot.get("game_build") is not None
            and value["game_build"] != snapshot["game_build"]):
        return blocked("economic_build_mismatched")
    time_fields = ("game_time_seconds", "time_before", "time_after", "sample_span_game_seconds")
    if not all(_number(value.get(key)) for key in time_fields):
        return blocked("economic_timing_unavailable")
    resources = value.get("resources")
    if type(resources) is not dict or not all(type(resources.get(key)) is dict for key in _ECONOMY_RESOURCES):
        return blocked("economic_resources_unavailable")
    known_guards = {"cache_refreshed_during_read", "stock_changed_during_read",
                    "game_time_rolled_back_during_read", "resource_capacity_changed_during_read"}
    source_guards = value.get("read_guard_issues")
    guards = []
    if type(source_guards) is list and len(source_guards) <= 100:
        guards = sorted({item for item in source_guards if type(item) is str and item in known_guards})
        if len(guards) != len(source_guards):
            unknown["economy.read_guard_issues"] = "unrecognized_guard_details_omitted"
    else:
        unknown["economy.read_guard_issues"] = "unavailable"
    if value["time_after"] < value["time_before"] or value["sample_span_game_seconds"] < 0:
        guards = sorted(set(guards) | {"game_time_rolled_back_during_read"})
    out = {"schema_version": 1, "status": "read_race" if guards else value["status"],
           "scope": "local_player_economic_caches", "source": "native_economic_caches_read_only",
           "hlboot_sha256": value["hlboot_sha256"], "resource_table_fingerprint": fingerprint,
           **{key: value[key] for key in time_fields},
           **{key: value[key] for key in _CAPTURE_IDENTITIES},
           "atomic": False, "read_guard_issues": guards, "resources": {},
           "rate_unit": "resource_per_game_second",
           "validation_status": "native_cache_semantics_verified_live_calibration_pending",
           "total_produced_semantics": "positive_default_reason_additions_not_a_worker_production_ledger",
           "quality": {"current_player_identity_verified": True, "identity_matches_snapshot": True,
                       "atomic": False, "live_calibration_complete": False}}
    scalar_fields = ("stock", "cached_at_game_time", "age_game_seconds",
                     "raw_cached_production_per_game_second", "consumption_per_game_second",
                     "net_per_game_second", "gross_aggregate_per_game_second",
                     "total_produced_counter", "stock_cap")
    native_fields = {"Food": "currentFood", "Wood": "currentWood", "Money": "currentMoney"}
    for resource in _ECONOMY_RESOURCES:
        raw = resources[resource]
        if not _number(raw.get("stock")):
            return blocked("economic_stock_unavailable")
        row = {}
        for key in scalar_fields:
            raw_number = raw.get(key)
            row[key] = raw_number if _number(raw_number) else None
            if row[key] is None:
                unknown["economy.resources." + resource + "." + key] = "unavailable"
        # A numeric cap alone never upgrades the counter/stock model. Require
        # the pinned formula and all of its own-building evidence explicitly.
        proof = raw.get('stock_cap_evidence')
        cap_verified = False
        if resource == 'Food' and type(proof) is dict:
            components = proof.get('components')
            own_silos = {item.get('uid'): item for item in snapshot.get('buildings', [])
                         if type(item) is dict and item.get('kind') == 'FoodSilo'
                         and item.get('zone_id') in snapshot.get('owned_zone_ids', [])}
            silos = components.get('silos') if type(components) is dict else None
            if (proof.get('hlboot_sha256') == _ECONOMY_BUILD_HASH
                    and proof.get('data_cdb_sha256') == _ECONOMY_DATA_HASH
                    and raw.get('stock_cap_verified') is True
                    and proof.get('stock_cap_verified') is True
                    and raw.get('stock_cap_source') == proof.get('stock_cap_source') == 'verified_native_getMax_formula'
                    and raw.get('integration_order') == proof.get('integration_order') == 'consumption_then_production'
                    and _number(row.get('stock_cap')) and row['stock_cap'] == proof.get('stock_cap')
                    and row['stock_cap'] > 0 and type(silos) is list
                    and _number(components.get('base')) and components['base'] > 0
                    and components.get('silo_multiplier') == 1.0):
                checked = []
                for silo in silos:
                    if (type(silo) is not dict or set(silo) != {'building_uid', 'zone_id', 'is_upgraded', 'capacity_added'}
                            or silo.get('building_uid') not in own_silos
                            or silo.get('zone_id') != own_silos[silo['building_uid']].get('zone_id')
                            or type(silo.get('is_upgraded')) is not bool
                            or not _number(silo.get('capacity_added')) or silo['capacity_added'] <= 0):
                        break
                    checked.append(dict(silo))
                else:
                    cap_verified = (len({s['building_uid'] for s in checked}) == len(checked)
                                    and {s['building_uid'] for s in checked} == set(own_silos)
                                    and row['stock_cap'] == int(components['base'] + sum(s['capacity_added'] for s in checked)))
                    if cap_verified:
                        row['stock_cap_components'] = {'base': components['base'], 'silos': checked}
        row['stock_cap_verified'] = cap_verified
        row['stock_cap_source'] = 'verified_native_getMax_formula' if cap_verified else 'unknown'
        row['stock_cap_status'] = 'finite_limit_verified' if cap_verified else 'not_verified'
        row['integration_order'] = 'consumption_then_production' if cap_verified else None
        if (raw.get('stock_cap') is None and raw.get('stock_cap_verified') is True
                and raw.get('stock_cap_source') == 'verified_native_no_finite_limit'
                and _unlimited_capacity_proof(proof, resource, snapshot)):
            row.update(stock_cap=None, stock_cap_verified=True,
                       stock_cap_source='verified_native_no_finite_limit',
                       stock_cap_status='no_finite_limit_verified', integration_order=None)
            unknown.pop('economy.resources.' + resource + '.stock_cap', None)
        if row["age_game_seconds"] is not None and row["age_game_seconds"] < 0:
            unknown["economy.resources." + resource + ".cache_freshness"] = "cache_timestamp_ahead_of_read_time"
        if raw.get("native_net_field") == native_fields[resource]:
            row["native_net_field"] = native_fields[resource]
        else:
            row["native_net_field"] = None
            unknown["economy.resources." + resource + ".native_net_field"] = "unverified"
        if raw.get("gross_aggregate_source") == "native_current_net_plus_native_consumption":
            row["gross_aggregate_source"] = "native_current_net_plus_native_consumption"
        else:
            row["gross_aggregate_source"] = None
            unknown["economy.resources." + resource + ".gross_aggregate_source"] = "unverified"
        # This label follows the verified Food update order, rather than an
        # untrusted nested source annotation. The base cache is never total food.
        row["food_base_cache_is_total"] = False if resource == "Food" else None
        out["resources"][resource] = row
    # All six stock limits have separate semantics even though continuous rate
    # integration is presently calibrated only for Food/Wood/Money.
    out['resource_limits'] = {}
    raw_limits = value.get('resource_limits', {}).get('resources', {}) if type(value.get('resource_limits')) is dict else {}
    for resource in CORE_RESOURCES:
        if resource in out['resources']:
            block = out['resources'][resource]
            out['resource_limits'][resource] = {key: block[key] for key in
                ('stock_cap', 'stock_cap_verified', 'stock_cap_source', 'stock_cap_status')}
        elif _unlimited_capacity_proof(raw_limits.get(resource), resource, snapshot):
            out['resource_limits'][resource] = {'stock_cap': None, 'stock_cap_verified': True,
                'stock_cap_source': 'verified_native_no_finite_limit', 'stock_cap_status': 'no_finite_limit_verified'}
        else:
            out['resource_limits'][resource] = {'stock_cap': None, 'stock_cap_verified': False,
                'stock_cap_source': 'unknown', 'stock_cap_status': 'not_verified'}
    unknown.pop("economy", None)
    if out["status"] == "read_race":
        unknown["economy.consistency"] = "concurrent_read_not_a_consistent_economic_sample"
    return out


def project_player_observation(snapshot: Any, *, include_own_events: bool = False) -> dict:
    """Return an independent whitelisted observation or an explicit rejection.

    Required identity attestation from the live reader::

        local_player_identity = {
            'verified': True, 'role': 'player', 'entity_uid': <me.__uid>,
            'is_ai': False, 'in_player_list': True,
        }

    The function never infers ownership from clan or research labels. Existing
    historical rows without this identity evidence must remain research data.
    No current-visibility source has been validated by this policy; even a
    caller-supplied visible_zone_ids list cannot unlock foreign tile objects.
    """
    if type(snapshot) is not dict or snapshot.get("status") != "ok":
        return _reject("source_not_ok")
    if snapshot.get("schema_version") != 2:
        return _reject("unsupported_source_schema")
    session = snapshot.get("session_key")
    if type(session) is not str or _SESSION.fullmatch(session) is None:
        return _reject("session_missing_or_invalid")
    own_uid = snapshot.get("local_player_entity_uid")
    identity = snapshot.get("local_player_identity")
    if (not _integer(own_uid, 1) or type(identity) is not dict
            or identity.get("verified") is not True
            or identity.get("role") != "player"
            or not _integer(identity.get("entity_uid"), 1)
            or identity.get("entity_uid") != own_uid
            or identity.get("is_ai") is not False
            or identity.get("in_player_list") is not True):
        return _reject("local_player_identity_unverified")
    if any(snapshot.get(key) is True for key in ("is_spectator", "spectator", "observer_mode")):
        return _reject("spectator_input_not_allowed")
    when = snapshot.get("game_time_seconds")
    stocks = snapshot.get("stocks")
    units, buildings = snapshot.get("units"), snapshot.get("buildings")
    owned, discovered = snapshot.get("owned_zone_ids"), snapshot.get("discovered_zone_ids")
    if (not _number(when) or when < 0 or not _token(snapshot.get("clan"))
            or type(stocks) is not dict
            or not all(_number(stocks.get(key)) for key in CORE_RESOURCES)
            or type(units) is not list or type(buildings) is not list
            or len(units) > 10000 or len(buildings) > 10000
            or not _ids(owned) or not _ids(discovered)
            or not set(owned) <= set(discovered)):
        return _reject("required_player_state_missing_or_invalid")
    if (not units and not buildings and not owned
            and snapshot.get("local_player_game_over") is not True):
        return _reject("empty_player_frame")
    known, own_zones = set(discovered), set(owned)
    own_entities = {}
    local_unit_zone_unverified = False
    for category, rows in (("own_unit", units), ("owned_zone_building", buildings)):
        for row in rows:
            if (type(row) is not dict or not _integer(row.get("uid"), 1)
                    or not _token(row.get("kind")) or not _owner_matches(row, own_uid)):
                return _reject("invalid_or_foreign_local_entity")
            uid, zid = row["uid"], row.get("zone_id")
            if uid in own_entities:
                return _reject("duplicate_local_entity_uid")
            if category == "owned_zone_building":
                if not _integer(zid) or zid not in own_zones:
                    return _reject("building_not_in_owned_zone")
            elif zid is not None and (not _integer(zid) or zid not in known):
                # A scout can enter a zone before exploration finishes. Its
                # ownership is still verified, so keep the unit but withhold
                # the unverified map identifier instead of losing the frame.
                zid = None
                local_unit_zone_unverified = True
            own_entities[uid] = {"entity_scope": category, "kind": row["kind"], "zone_id": zid}
    unknown = {"map.current_visibility": "unverified_non_owned_objects_omitted",
               "map.last_seen_state": "not_recorded",
               "unit.job_arguments": "not_verified_or_exported",
               "economy": "not_projected_until_field_semantics_are_verified"}
    if local_unit_zone_unverified:
        unknown["units.zone_id"] = "not_in_discovered_set_omitted"
    result = {"schema_version": 1, "observation_type": "PlayerObservation",
              "policy_version": POLICY_VERSION, "status": "ok",
              "scope": "local_player_and_owned_zones",
              "session_key": session, "local_player_entity_uid": own_uid,
              "game_time_seconds": when, "clan": snapshot["clan"],
              "stocks": {key: stocks[key] for key in CORE_RESOURCES},
              "owned_zone_ids": sorted(owned), "discovered_zone_ids": sorted(discovered),
              "units": [], "buildings": [], "tiles": [],
              "unknown_fields": unknown,
              "quality": {"atomic": False, "identity_verified": True,
                          "current_visibility_verified": False,
                          "native_events_policy": "own_schema_whitelist" if include_own_events else "disabled"}}
    for key in ("capture_run_id", "capture_segment_id"):
        identifier = snapshot.get(key)
        if type(identifier) is str and _SESSION.fullmatch(identifier) is not None:
            result[key] = identifier
        else:
            unknown[key] = "unavailable"
    captured = snapshot.get("captured_at")
    if type(captured) is str:
        try:
            result["captured_at"] = datetime.fromisoformat(captured).isoformat()
        except ValueError:
            unknown["captured_at"] = "invalid_timestamp_omitted"
    for field in ("sample_span_game_seconds", "sample_duration_wall_seconds", "population", "population_cap"):
        value = snapshot.get(field)
        if _number(value) and value >= 0:
            result[field] = value
        else:
            result[field] = None
            unknown[field] = "unavailable"
    if population_cap_bootstrap_unavailable(snapshot):
        result['population_cap'] = None
        unknown['population_cap'] = 'bootstrap_cache_not_initialized'
    if type(snapshot.get("in_winter")) is bool:
        result["in_winter"] = snapshot["in_winter"]
    else:
        unknown["in_winter"] = "unavailable"
    context = snapshot.get('game_context')
    if type(context) is dict:
        scoped_context = {}
        for source, target in (('mode', 'mode_kind'), ('game_type', 'game_type_kind')):
            value = context.get(source)
            if type(value) is dict and _token(value.get('kind')):
                scoped_context[target] = value['kind']
        mode = context.get('mode')
        if type(mode) is dict and type(mode.get('args')) is list and len(mode['args']) == 1:
            variant = mode['args'][0]
            if type(variant) is dict and _token(variant.get('kind')):
                scoped_context['mode_variant_kind'] = variant['kind']
        for key in ('bifrost_data_present', 'editor_present'):
            if type(context.get(key)) is bool:
                scoped_context[key] = context[key]
        if _number(context.get('difficulty')):
            scoped_context['difficulty'] = context['difficulty']
        if _token(context.get('biome')):
            scoped_context['biome'] = context['biome']
        result['game_context'] = scoped_context
    for field in ("session_game_finished", "local_player_game_over", "local_player_has_conceded"):
        if type(snapshot.get(field)) is bool:
            result[field] = snapshot[field]
    for row in units:
        out = {"uid": row["uid"], "kind": row["kind"], "zone_id": own_entities[row["uid"]]["zone_id"]}
        for key in ("assigned_job", "cur_job"):
            out[key] = _job(row.get(key), own_entities, known, unknown, "units." + key)
        for key in ("assign_to", "previous_assign_to", "going_to"):
            out[key] = _entity_reference(row.get(key), own_entities, known, unknown, "units." + key)
        target = row.get("target_zone_id")
        out["target_zone_id"] = target if _integer(target) and target in known else None
        if target is not None and out["target_zone_id"] is None:
            unknown["units.target_zone_id"] = "undiscovered_target_omitted"
        for key in ("has_no_work", "just_assigned", "enabled", "selected"):
            if type(row.get(key)) is bool:
                out[key] = row[key]
        if row.get("produce") in CORE_RESOURCES + ("Wheat", "Fish", "Meat", "Sailors", "Healing"):
            out["produce"] = row["produce"]
        else:
            out["produce"] = None
            unknown["units.produce"] = "unsupported_resource_or_unavailable"
        if _number(row.get("hit_life")):
            out["hit_life"] = row["hit_life"]
        out["position"] = _position(row.get("position"))
        result["units"].append(out)
    for row in buildings:
        out = {"uid": row["uid"], "kind": row["kind"], "zone_id": row["zone_id"]}
        for key in ("base_kind", "real_kind", "target_kind"):
            out[key] = row.get(key) if _token(row.get(key)) else None
        for key in ("is_construction", "wait_repair", "marked_for_destroy", "selected"):
            if type(row.get(key)) is bool:
                out[key] = row[key]
        for key in ("upgraded", "construction_progress", "hit_life"):
            out[key] = row.get(key) if _number(row.get(key)) else None
        assigned = row.get("assigned_unit_ids")
        out["assigned_unit_ids"] = [uid for uid in assigned if _integer(uid)
            and uid in own_entities and own_entities[uid]["entity_scope"] == "own_unit"] if type(assigned) is list else []
        out["position"] = _position(row.get("position"))
        # Building.state contains unverified dynamic slots and is not copied.
        result["buildings"].append(out)
    tiles = snapshot.get("discovered_tiles")
    tile_by_id = {}
    if type(tiles) is list:
        for tile in tiles:
            if type(tile) is dict and _integer(tile.get("id")):
                tile_by_id[tile["id"]] = tile
    for zid in sorted(discovered):
        out = {"id": zid}
        if zid not in own_zones:
            out.update({"knowledge_scope": "discovered_id_only", "current_visibility": "unverified"})
        else:
            out.update({"knowledge_scope": "owned_zone", "owner": "self"})
            tile = tile_by_id.get(zid, {})
            slots = tile.get("base_building_slots")
            if _integer(slots) and slots <= 1000:
                out["base_building_slots"] = slots
            else:
                unknown["tiles.base_building_slots"] = "unavailable"
            # Derived from separately ownership-checked buildings, rather than
            # forwarding the discovered tile's unverified dynamic object list.
            out["buildings"] = [b["kind"] for b in result["buildings"] if b["zone_id"] == zid]
        result["tiles"].append(out)
    result["own_events"] = []
    if include_own_events:
        events = snapshot.get("native_events")
        if type(events) is list and len(events) <= 100000:
            for event in events:
                projected = _own_event(event, own_uid, known, when)
                if projected is not None:
                    result["own_events"].append(projected)
        else:
            unknown["own_events"] = "native_event_list_unavailable"
    if "economy" in snapshot:
        result["economy"] = _project_economy(snapshot["economy"], snapshot, unknown)
    if "operations" in snapshot:
        result['operations'] = project_operations(snapshot['operations'], snapshot)
    if 'map_observation' in snapshot:
        result['map_observation'] = project_map(snapshot['map_observation'], snapshot)
    if 'labor' in snapshot:
        result['labor'] = project_labor(snapshot['labor'], snapshot)
    if 'population_growth' in snapshot:
        result['population_growth'] = project_growth(snapshot['population_growth'], snapshot)
    if 'match_context' in snapshot:
        result['match_context'] = project_match_context(snapshot['match_context'], snapshot)
    if 'known_zone_details' in snapshot:
        result['known_zone_details'] = project_known_zone_details(snapshot['known_zone_details'], snapshot)
    return result


def action_diff_observation(observation: Any) -> dict:
    """Adapt only the verified own-event projection for the action differ.

    PlayerObservation deliberately omits arbitrary native params. The legacy
    differ requires an actor in params to recognize local events, so passing
    own_events straight through silently discarded every success event. These
    params are a minimal *projection*, not the original full native log. In
    particular no lost geometry, native tech level or foreign actor is invented.
    """
    if (type(observation) is not dict
            or observation.get("observation_type") != "PlayerObservation"
            or observation.get("policy_version") != POLICY_VERSION
            or observation.get("status") != "ok"
            or observation.get("scope") != "local_player_and_owned_zones"
            or type(observation.get("quality")) is not dict
            or observation["quality"].get("identity_verified") is not True
            or not _integer(observation.get("local_player_entity_uid"), 1)
            or not _number(observation.get("game_time_seconds"))
            or not _ids(observation.get("discovered_zone_ids"))
            or any(type(observation.get(k)) is not str
                   or _SESSION.fullmatch(observation[k]) is None for k in _CAPTURE_IDENTITIES)):
        raise ValueError("Action differ requires a verified identified PlayerObservation")
    result = dict(observation)
    result["native_events"] = []
    result["native_events_schema"] = "sanitized_own_success_projection.v1"
    if observation.get("quality", {}).get("native_events_policy") != "own_schema_whitelist":
        return result
    candidates = observation.get("own_events")
    if type(candidates) is not list or len(candidates) > 100000:
        return result
    uid, when = observation["local_player_entity_uid"], observation["game_time_seconds"]
    known = set(observation["discovered_zone_ids"])
    actor = {"entity_type": "ent.Player", "entity_uid": uid}
    simple = {"Feast", "Defeat", "Victory", "Overwork", "Ignite"}
    zone_kinds = {"Discover", "Colonize", "Uncolonize", "Explore", "AddBuilding"}
    tech_kinds = {"LearnTech", "LearnFreeTech"}
    unit_kinds = {"HireWarchief", "UpgradeUnit"}
    base_keys = {"index", "time", "kind", "actor", "source"}
    seen = set()
    for event in candidates:
        if (type(event) is not dict or event.get("actor") != "self"
                or event.get("source") != "native_own_success_event"
                or not _integer(event.get("index")) or not _number(event.get("time"))
                or not 0 <= event["time"] <= when + 1):
            continue
        kind = event.get("kind")
        if not _token(kind):
            continue
        params = [dict(actor)]
        extra_keys = set()
        if kind in zone_kinds:
            if not _integer(event.get("zone_id")) or event["zone_id"] not in known:
                continue
            params.append({"entity_type": "ent.Zone", "zone_id": event["zone_id"]})
            extra_keys.add("zone_id")
            if kind == "Uncolonize":
                if type(event.get("native_flag")) is not bool:
                    continue
                params.append(event["native_flag"])
                extra_keys.add("native_flag")
            if kind in {"Explore", "AddBuilding"}:
                if not _token(event.get("building_kind")):
                    continue
                params.append(event["building_kind"])
                extra_keys.add("building_kind")
            if kind == "AddBuilding":
                # Missing position stays unknown: completion matching must still
                # reject multiple possible constructions in the same zone.
                params.append(None)
        elif kind in tech_kinds | unit_kinds:
            key = "tech_id" if kind in tech_kinds else "unit_or_upgrade_kind"
            if not _token(event.get(key)):
                continue
            extra_keys.add(key)
            params.append(event[key])
        elif kind not in simple:
            continue
        identity_keys = set(_CAPTURE_IDENTITIES) | {"local_player_entity_uid"}
        if (set(event) - base_keys - extra_keys - identity_keys
                or any(k in event and event[k] != observation.get(k) for k in identity_keys)):
            continue
        if event["index"] in seen:
            # Ambiguous projected history is unusable, rather than guessing
            # which payload belongs to the original log index.
            result["native_events"] = []
            return result
        seen.add(event["index"])
        result["native_events"].append({**event, "params": params,
            "params_schema": "sanitized_own_success_projection.v1",
            **{k: observation[k] for k in _CAPTURE_IDENTITIES},
            "local_player_entity_uid": uid})
    return result
