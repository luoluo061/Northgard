"""Build explicit, reproducible source and capture ZIPs using the standard library.

No directory recursion, game files, existing captures or network operations are
part of a source release. With no mode flag the command only checks readiness.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import zipfile


VERSION = "0.5.3"
ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    'docs/RECORDING_AND_REVIEW.zh-CN.md',
    'scripts/summarize_capture.py',
    'tests/test_capture_summary.py',
    'tests/test_collector_finalization.py',
    'docs/REALTIME_ASSISTANT_REFERENCES.zh-CN.md',
    'docs/PROJECT_ROADMAP.zh-CN.md',
    'docs/MAP_OBJECT_CAPTURE_SPEC.zh-CN.md',
    'scripts/inspect_map_catalog.py',
    'tests/test_map_catalog.py',
    'docs/OPERATIONS_ADVISOR_DESIGN.zh-CN.md',
    'docs/KNOWN_ZONE_DETAILS.zh-CN.md',
    'companion/core/known_zone_details.py',
    'companion/tests/test_known_zone_details.py',
    'research/telemetry/known_zone_capture.py',
    'research/telemetry/test_known_zone_capture.py',
    'research/telemetry/test_known_zone_integration.py',
    'tests/test_known_zone_submission.py',
    'tests/test_known_zone_collector.py',
    'tests/test_known_zone_report.py',
    'scripts/show_known_zones.py',
    'docs/MECHANICS_REFERENCE.zh-CN.md',
    'docs/POPULATION_ECONOMY.zh-CN.md',
    'docs/JOBS_REFERENCE.zh-CN.md',
    'docs/ECONOMIC_CONVERSIONS.zh-CN.md',
    'docs/BEAR_KNOWLEDGE_REFERENCE.zh-CN.md',
    'mechanics/catalog/population_economy.v1.json',
    'mechanics/catalog/common_jobs.v1.json',
    'mechanics/catalog/conversions.v1.json',
    'mechanics/catalog/bear_knowledge.v1.json',
    'mechanics/baseline.py',
    'mechanics/test_baseline.py',
    'mechanics/rules.v1.json',
    'mechanics/README.md',
    'docs/MECHANICS_FRAMEWORK.zh-CN.md',
    'docs/MECHANICS_RESEARCH_PROGRAM.zh-CN.md',
    'docs/STRATEGY_ROLES_AND_GUIDES.zh-CN.md',
    'docs/CLAN_STRATEGY_CASES.zh-CN.md',
    'docs/MECHANICS_EVIDENCE.md',
    'docs/MECHANICS_VALIDATION_PLAN.md',
    'tests/test_atomic_publication.py',
    'companion/core/base_rings.py',
    'companion/tests/test_base_rings.py',
    'tests/test_base_ring_collector.py',
    'tests/test_base_neighborhood_submission.py',
    'docs/BASE_NEIGHBORHOOD.md',
    'research/telemetry/test_discovered_tile_scope.py',
    'research/telemetry/test_map_wrappers.py',
    'tests/test_component_diagnostics.py',
    'research/telemetry/match_context.py',
    'companion/core/match_context_projection.py',
    'research/telemetry/test_match_context.py',
    'schemas/metadata.multiplayer.example.json',
    '.github/ISSUE_TEMPLATE/collector-problem.yml',
    'CONTRIBUTING.md',
    'scripts/create_match_kit.py',
    'tests/test_match_kit.py',
    'tests/test_collector_match_labels.py',
    'docs/MULTIPLAYER_3V3.md',
    '.github/workflows/offline-tests.yml',
    '.github/workflows/validate-submissions.yml',
    '.github/ISSUE_TEMPLATE/player-experience.yml',
    'data/catalog.json',
    'data/submissions/.gitkeep',
    'research/telemetry/record_choice.py',
    'companion/examples/food_before_winter.json',
    'companion/examples/raven_money_colonization.json',
    'scripts/export_submission_schemas.py',
    'schemas/README.md',
    'collector/collect.py',
    'collector/Start-Collector.ps1',
    'collector/README.zh-CN.md',
    'collector/INSTALL_WITH_AI.md',
    'collector/build_release.py',
    'companion/core/player_observation.py',
    'companion/core/capture_identity.py',
    'companion/core/action_diff.py',
    'companion/core/economy_reconcile.py',
    'companion/core/operation_projection.py',
    'companion/core/operation_ledger.py',
    'companion/core/map_projection.py',
    'companion/core/situation_timeline.py',
    'research/runtime/live_reader.py',
    'research/runtime/winmem.py',
    'research/runtime/runtime_types.py',
    'research/runtime/population_read.py',
    'research/runtime/build_profile.json',
    'research/inspect_northgard_assets.py',
    'research/telemetry/action_capture.py',
    'research/telemetry/runtime_decode.py',
    'research/telemetry/economy_capture.py',
    'research/telemetry/resource_caps.py',
    'research/telemetry/operation_capture.py',
    'research/telemetry/cost_quotes.py',
    'research/telemetry/map_capture.py',
    'research/telemetry/labor_capture.py',
    'research/telemetry/growth_capture.py',
    'companion/core/labor_projection.py',
    'companion/core/growth_projection.py',
    'companion/core/labor_transitions.py',
    'scripts/setup_local.py',
    'scripts/doctor.py',
    'scripts/local_rules.py',
    'scripts/prepare_submission.py',
    'scripts/validate_submission.py',
    'scripts/build_catalog.py',
    'scripts/submission_contract.py',
    'AGENTS.md',
    'README.md',
    '.gitignore',
    'docs/INSTALL_WITH_CODEX.md',
    'docs/GAME_DATA_GUIDE.zh-CN.md',
    'docs/UNIT_PRODUCTION_LIMITS.md',
    'docs/VALIDATION_STATUS.md',
    'docs/DATA_CONTRIBUTION.md',
    'docs/PLAYER_FEEDBACK.md',
    'docs/GROWTH_AND_TRANSITIONS.md',
    'schemas/metadata.example.json',
    'companion/core/match_review.py',
    'companion/core/recommender.py',
    'companion/tests/test_action_diff.py',
    'companion/tests/test_action_entity_ref.py',
    'companion/tests/test_boundary_integration.py',
    'companion/tests/test_capacity_projection.py',
    'companion/tests/test_capacity_reconcile.py',
    'companion/tests/test_capture_identity.py',
    'companion/tests/test_choice_identity.py',
    'companion/tests/test_collector_lifecycle.py',
    'companion/tests/test_economy_reconcile.py',
    'companion/tests/test_growth_projection.py',
    'companion/tests/test_labor_transitions.py',
    'companion/tests/test_map_projection.py',
    'companion/tests/test_operation_ledger.py',
    'companion/tests/test_operation_pipeline.py',
    'companion/tests/test_operation_projection.py',
    'companion/tests/test_player_observation.py',
    'companion/tests/test_projected_action_pipeline.py',
    'companion/tests/test_recommender.py',
    'companion/tests/test_situation_timeline.py',
    'research/telemetry/test_cost_quotes.py',
    'research/telemetry/test_economy_capture.py',
    'research/telemetry/test_growth_capture.py',
    'research/telemetry/test_labor_capture.py',
    'research/telemetry/test_map_capture.py',
    'research/telemetry/test_operation_capture.py',
    'research/telemetry/test_optional_components.py',
    'research/telemetry/test_resource_caps.py',
    'collector/test_build_release.py',
    'collector/test_export_stream.py',
    'tests/test_setup_local.py',
    'tests/test_submission_pipeline.py',
    'scripts/run_tests.py',
    'CHANGELOG.md',
    'data/README.md',
    'schemas/capture-manifest-v1.schema.json',
    'schemas/metadata-v1.schema.json',
    'schemas/record-actions-v1.schema.json',
    'schemas/record-economic-snapshots-v1.schema.json',
    'schemas/record-health-v1.schema.json',
    'schemas/record-player-observations-v1.schema.json',
    'schemas/submission-v1.schema.json',
)
CAPTURE_FILES = ("player-observations.ndjson", "economic-snapshots.ndjson", "actions.ndjson",
                 "health.ndjson", "manifest.json", "latest.json")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|^\\\\|^/Users/|^/home/)")
_DENIED_CAPTURE_KEYS = {
    "pid", "name", "username", "account", "account_id", "steam_id", "steamid", "player_name",
    "opponent_name", "raw_resource_map", "raw_resource_slots", "total_produced_counters",
    "native_events", "players", "ai_goals", "goals", "game_path", "game_root", "install_path",
    "exe_path", "pointer", "address",
}
CAPTURE_MAX_FILE_BYTES = 4 * 1024 ** 3
CAPTURE_MAX_TOTAL_BYTES = 8 * 1024 ** 3
CAPTURE_MAX_RECORD_BYTES = 8 * 1024 ** 2
STREAM_CHUNK_BYTES = 1024 ** 2


class BuildError(ValueError):
    pass


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _load_json(raw):
    def invalid_constant(_):
        raise BuildError("Nonfinite JSON values are not accepted")
    try:
        return json.loads(raw.decode("utf-8-sig"), parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        if isinstance(error, BuildError):
            raise
        raise BuildError("Invalid or excessively nested JSON record") from error


def _source_path(root, relative):
    path = root / relative
    if not path.is_file():
        raise BuildError("Missing required file: " + relative)
    if not path.resolve().is_relative_to(root.resolve()):
        raise BuildError("Source link escapes the release root: " + relative)
    return path


def minimize_static(relative, raw):
    """Retain exactly the fields used by shipped population/resource decoders."""
    original = _load_json(raw)
    rows = original.get("rows") if type(original) is dict else None
    if type(rows) is not list or not rows:
        raise BuildError("Missing static definition rows: " + relative)
    output = []
    for row in rows:
        if type(row) is not dict or type(row.get("id")) is not str or not row["id"]:
            raise BuildError("Invalid static definition identifier: " + relative)
        props = row.get("props", {})
        if type(props) is not dict:
            raise BuildError("Invalid static definition properties: " + relative)
        if relative.endswith("/unit.json"):
            bits = row.get("types", 0)
            if type(bits) is not int:
                raise BuildError("Invalid population type bits")
            output.append({"id": row["id"], "types": bits,
                           "props": {"canBefriendTech": bool(props.get("canBefriendTech"))}})
        else:
            index = row.get("index", row.get("_row_index"))
            if type(index) is not int or not 0 <= index < 512:
                raise BuildError("Invalid explicit resource index")
            # Keep absent values as null, preserving resource_schema's canonical
            # meaning without translations, images or other asset references.
            output.append({"id": row["id"], "_row_index": index,
                           "isProductible": row.get("isProductible", row.get("is_productible")),
                           "props": {"converts": props.get("converts", row.get("converts"))}})
    if len({row["id"] for row in output}) != len(output):
        raise BuildError("Duplicate static definition identifier")
    return _json_bytes({"schema_version": 1, "purpose": "minimal_collector_runtime_definition", "rows": output})


def source_payload(root):
    root = Path(root).resolve()
    payload = {}
    for relative in SOURCE_FILES:
        raw = _source_path(root, relative).read_bytes()
        if relative.endswith(".py"):
            try:
                ast.parse(raw, filename=relative)
            except SyntaxError as error:
                raise BuildError("Python syntax not ready: " + relative) from error
        if relative in ("research/static/unit.json", "research/static/resource.json"):
            raw = minimize_static(relative, raw)
        payload[relative] = raw
    profile = _load_json(payload["research/runtime/build_profile.json"])
    if any(type(profile.get(key)) is not str or _SHA.fullmatch(profile[key]) is None
           for key in ("hlboot_sha256", "data_cdb_sha256")):
        raise BuildError("Release requires both verified hlboot_sha256 and data_cdb_sha256")
    if profile.get("pointer_size") != 8:
        raise BuildError("Release is restricted to the verified 64-bit profile")
    release = {"schema_version": 1, "artifact_type": "northgard_collector_source_release",
               "version": VERSION, "stage": "alpha", "network_upload": False,
               "runtime": {"platform": "Windows", "architecture": "64-bit", "python_minimum": "3.11",
                           "third_party_python_dependencies": []},
               "verified_game": {key: profile.get(key) for key in
                   ("game_build", "window_version", "hlboot_sha256", "data_cdb_sha256", "pointer_size")},
               "files": [{"path": path, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                         for path, data in sorted(payload.items())],
               "capture_privacy": "No existing captures, game assets, saved games, names or local paths are bundled."}
    payload["release-manifest.json"] = _json_bytes(release)
    return payload


def _check_share_value(value, depth=0):
    if depth > 64:
        raise BuildError("Capture record nesting is too deep")
    if type(value) is dict:
        for key, item in value.items():
            if key.lower() in _DENIED_CAPTURE_KEYS or (key == "research_only" and item is True):
                raise BuildError("Capture contains a field outside the sharing policy")
            _check_share_value(item, depth + 1)
    elif type(value) is list:
        for item in value:
            _check_share_value(item, depth + 1)
    elif type(value) is str and _ABSOLUTE_PATH.search(value):
        raise BuildError("Capture contains an absolute local path; review the export before sharing")
    elif type(value) is float and not math.isfinite(value):
        raise BuildError("Nonfinite JSON values are not accepted")


def _file_signature(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _check_capture_unchanged(path, expected):
    if _file_signature(path.stat()) != expected:
        raise BuildError("Capture changed while packaging; stop collection first")


def _stream_capture_member(path, relative, expected, *, max_record_bytes,
                           destination=None, validate=True, parsed_json=None):
    """Bounded reads: one NDJSON record during validation, fixed chunks on copy."""
    digest = hashlib.sha256()
    size = 0
    json_value = None
    _check_capture_unchanged(path, expected)
    with path.open("rb") as handle:
        # On Windows Path.stat and os.fstat can expose different st_ctime
        # semantics. Compare identity/size/mtime on the handle; the path's
        # complete signature is checked separately before and after the read.
        if _file_signature(os.fstat(handle.fileno()))[:4] != expected[:4]:
            raise BuildError("Capture file identity changed while opening")
        if validate and not relative.endswith(".ndjson"):
            raw = handle.read(max_record_bytes + 1)
            if len(raw) > max_record_bytes:
                raise BuildError("Capture JSON exceeds the record-size limit: " + relative)
            json_value = _load_json(raw)
            _check_share_value(json_value)
            if parsed_json is not None:
                parsed_json.append(json_value)
            chunks = (raw,)
        else:
            read = ((lambda: handle.readline(max_record_bytes + 1)) if validate else
                    (lambda: handle.read(STREAM_CHUNK_BYTES)))
            chunks = iter(read, b"")
        for raw in chunks:
            size += len(raw)
            if size > expected[2]:
                raise BuildError("Capture grew while packaging; stop collection first")
            if validate and relative.endswith(".ndjson"):
                if len(raw) > max_record_bytes:
                    raise BuildError("Capture NDJSON record exceeds the record-size limit: " + relative)
                if raw.strip():
                    _check_share_value(_load_json(raw))
            digest.update(raw)
            if destination is not None:
                destination.write(raw)
        if _file_signature(os.fstat(handle.fileno()))[:4] != expected[:4] or size != expected[2]:
            raise BuildError("Capture changed while packaging; stop collection first")
    _check_capture_unchanged(path, expected)
    if relative == "manifest.json" and validate:
        if type(json_value) is not dict or json_value.get("recording_state") != "stopped":
            raise BuildError("Capture manifest must explicitly say recording_state=stopped; stop collection first")
    return {"path": relative, "size_bytes": size, "sha256": digest.hexdigest()}


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(STREAM_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_entry(relative):
    parts = relative.split("/")
    if relative.startswith("/") or ".." in parts or any(":" in part or "\\" in part for part in parts):
        raise BuildError("Unsafe archive member name")
    entry = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
    entry.compress_type = zipfile.ZIP_DEFLATED
    entry.external_attr = 0o100644 << 16
    return entry


def write_capture_archive(directory, output, *, max_file_bytes=CAPTURE_MAX_FILE_BYTES,
                          max_total_bytes=CAPTURE_MAX_TOTAL_BYTES,
                          max_record_bytes=CAPTURE_MAX_RECORD_BYTES):
    """Validate and archive a stopped capture without retaining complete files.

    First pass validates bounded records and hashes bytes. Second pass streams
    the ZIP and requires the same hashes. File identity/size/timestamps must
    remain unchanged throughout. Only the six explicit export files are read.
    """
    directory = Path(directory).resolve()
    output = Path(output)
    checksum = output.with_name(output.name + ".sha256")
    listing = output.with_name(output.stem + ".files.sha256")
    temporary = output.with_name(output.name + ".tmp")
    checksum_tmp = checksum.with_name(checksum.name + ".tmp")
    listing_tmp = listing.with_name(listing.name + ".tmp")
    targets = (output, checksum, listing)
    temporaries = (temporary, checksum_tmp, listing_tmp)
    if any(path.exists() for path in targets + temporaries):
        raise BuildError("Output or temporary output already exists; choose a new output directory")
    if any(type(value) is not int or value <= 0 for value in
           (max_file_bytes, max_total_bytes, max_record_bytes)):
        raise BuildError("Capture limits must be positive byte counts")
    paths, signatures = {}, {}
    for relative in CAPTURE_FILES:
        path = _source_path(directory, relative)
        signature = _file_signature(path.stat())
        if signature[2] > max_file_bytes:
            raise BuildError("Capture file exceeds the sharing file-size limit: " + relative)
        paths[relative], signatures[relative] = path, signature
    if sum(signature[2] for signature in signatures.values()) > max_total_bytes:
        raise BuildError("Capture exceeds the total sharing-size limit")
    # Refuse an active collector before scanning its potentially large files.
    manifest_holder = []
    _stream_capture_member(paths["manifest.json"], "manifest.json", signatures["manifest.json"],
                           max_record_bytes=max_record_bytes, parsed_json=manifest_holder)
    source_version = manifest_holder[0].get("collector_version")
    if type(source_version) is not str or not source_version:
        source_version = "unknown"
    metadata = {}
    for relative in sorted(paths):
        metadata[relative] = _stream_capture_member(paths[relative], relative, signatures[relative],
                                                  max_record_bytes=max_record_bytes)
    share_manifest = _json_bytes({
        "schema_version": 1, "artifact_type": "northgard_collector_shared_capture",
        "collector_version": source_version, "source_collector_version": source_version,
        "exporter_version": VERSION, "source": "explicit_local_export_file_whitelist",
        "research_raw_included": False, "automatically_uploaded": False,
        "files": [metadata[relative] for relative in sorted(metadata)],
        "export_limits_bytes": {"single_file": max_file_bytes, "total_source": max_total_bytes,
                                "single_record": max_record_bytes}})
    manifest_hash = hashlib.sha256(share_manifest).hexdigest()
    sums = "".join(metadata[relative]["sha256"] + "  " + relative + "\n" for relative in sorted(metadata))
    sums += manifest_hash + "  share-manifest.json\n"
    sums = sums.encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    owned_temporaries, published = [], []
    try:
        # Exclusive open never truncates a file created by another exporter.
        with temporary.open("xb") as zip_handle:
            owned_temporaries.append(temporary)
            with zipfile.ZipFile(zip_handle, "w", compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=9, allowZip64=True) as archive:
                for relative in sorted(paths):
                    entry = _archive_entry(relative)
                    entry.file_size = metadata[relative]["size_bytes"]
                    with archive.open(entry, "w", force_zip64=entry.file_size >= zipfile.ZIP64_LIMIT) as destination:
                        actual = _stream_capture_member(paths[relative], relative, signatures[relative],
                            max_record_bytes=max_record_bytes, destination=destination, validate=False)
                    if actual != metadata[relative]:
                        raise BuildError("Capture content changed between validation and ZIP writing")
                archive.writestr(_archive_entry("share-manifest.json"), share_manifest)
                archive.writestr(_archive_entry("SHA256SUMS.txt"), sums)
        archive_hash = _hash_file(temporary)
        for path, data in ((checksum_tmp, (archive_hash + "  " + output.name + "\n").encode("utf-8")),
                           (listing_tmp, sums)):
            with path.open("xb") as handle:
                owned_temporaries.append(path)
                handle.write(data)
        for relative in paths:
            _check_capture_unchanged(paths[relative], signatures[relative])
        # Atomic publication without replacing an existing target. The ZIP is
        # published last; on failure remove only outputs this invocation owns.
        for staged, final in ((checksum_tmp, checksum), (listing_tmp, listing), (temporary, output)):
            os.link(staged, final)
            published.append(final)
            staged.unlink()
            owned_temporaries.remove(staged)
        return {"archive": output.name, "sha256": archive_hash, "member_count": len(paths) + 2,
                "archive_bytes": output.stat().st_size, "source_bytes": sum(s[2] for s in signatures.values()),
                "checksum_file": checksum.name, "file_checksums": listing.name,
                "streaming_export": True}
    except BaseException:
        for path in published:
            path.unlink(missing_ok=True)
        raise
    finally:
        for path in owned_temporaries:
            path.unlink(missing_ok=True)


def capture_payload(directory):
    """Read only the six product exports, refusing obvious research/identity data."""
    directory = Path(directory).resolve()
    payload = {}
    source_version = "unknown"
    for relative in CAPTURE_FILES:
        path = _source_path(directory, relative)
        before = path.stat()
        if before.st_size > 256 * 1024 * 1024:
            raise BuildError("Capture file exceeds the 256 MiB sharing limit: " + relative)
        raw = path.read_bytes()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise BuildError("Capture changed while packaging; stop collection first")
        records = raw.decode("utf-8-sig").splitlines() if relative.endswith(".ndjson") else [raw]
        for record in records:
            if not record:
                continue
            parsed = _load_json(record.encode("utf-8") if type(record) is str else record)
            _check_share_value(parsed)
        if relative == "manifest.json":
            manifest = _load_json(raw)
            if manifest.get("recording_state") != "stopped":
                raise BuildError("Capture manifest must explicitly say recording_state=stopped; stop collection first")
            version = manifest.get("collector_version")
            source_version = version if type(version) is str and version else "unknown"
        payload[relative] = raw
    payload["share-manifest.json"] = _json_bytes({
        "schema_version": 1, "artifact_type": "northgard_collector_shared_capture",
        "collector_version": source_version, "source_collector_version": source_version,
        "exporter_version": VERSION, "source": "explicit_local_export_file_whitelist",
        "research_raw_included": False, "automatically_uploaded": False,
        "files": [{"path": path, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                  for path, raw in sorted(payload.items())]})
    return payload


def write_archive(payload, output):
    """Create a new ZIP and two SHA files; never replace an existing artifact."""
    output = Path(output)
    checksum = output.with_name(output.name + ".sha256")
    listing = output.with_name(output.stem + ".files.sha256")
    if any(path.exists() for path in (output, checksum, listing)):
        raise BuildError("Output already exists; choose a new output directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    sums = "".join(hashlib.sha256(raw).hexdigest() + "  " + path + "\n"
                   for path, raw in sorted(payload.items())).encode("utf-8")
    all_files = {**payload, "SHA256SUMS.txt": sums}
    temporary = output.with_name(output.name + ".tmp")
    if temporary.exists():
        raise BuildError("Temporary output already exists; inspect it before retrying")
    try:
        with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for relative, raw in sorted(all_files.items()):
                parts = relative.split("/")
                if relative.startswith("/") or ".." in parts or any(":" in part or "\\" in part for part in parts):
                    raise BuildError("Unsafe archive member name")
                entry = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, raw, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        temporary.replace(output)
        checksum.write_text(hashlib.sha256(output.read_bytes()).hexdigest() + "  " + output.name + "\n", encoding="utf-8")
        listing.write_bytes(sums)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return {"archive": output.name, "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "member_count": len(all_files), "archive_bytes": output.stat().st_size,
            "checksum_file": checksum.name, "file_checksums": listing.name}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--build", "--source", action="store_true", help="Explicitly create a source ZIP; local game definitions are generated after installation")
    modes.add_argument("--check-only", action="store_true", help="Check source release readiness (default)")
    modes.add_argument("--share-capture", type=Path, help="Legacy local export; repository contributions must use scripts/prepare_submission.py")
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--out-dir", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        if args.share_capture:
            result = write_capture_archive(args.share_capture,
                args.out_dir / ("northgard-capture-" + VERSION + ".zip"))
        else:
            payload = source_payload(args.source_root)
            result = {"status": "ready", "version": VERSION, "payload_files": len(payload),
                      "source_release_created": False}
            if args.build:
                result = write_archive(payload, args.out_dir / ("collector_v" + VERSION + ".zip"))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (BuildError, OSError, UnicodeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "not_ready", "reason": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
