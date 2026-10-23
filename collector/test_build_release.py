import importlib.util
import json
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


SPEC = importlib.util.spec_from_file_location("build_release", Path(__file__).with_name("build_release.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def source_fixture(root):
    for relative in MODULE.SOURCE_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n" if relative.endswith(".py") else "fixture\n", encoding="utf-8")
    (root / "research/runtime/build_profile.json").write_text(json.dumps({
        "hlboot_sha256": "a" * 64, "data_cdb_sha256": "b" * 64, "pointer_size": 8,
        "game_build": "25096976"}), encoding="utf-8")
    (root / "research/static").mkdir(parents=True, exist_ok=True)
    (root / "research/static/unit.json").write_text(json.dumps({"rows": [{
        "id": "Villager", "types": 4, "props": {"canBefriendTech": "Atech", "secret": "omit"},
        "gfx": {"file": "secret_asset.png"}, "texts": {"name": "omit"}}]}), encoding="utf-8")
    (root / "research/static/resource.json").write_text(json.dumps({"rows": [{
        "id": "Food", "_row_index": 0, "isProductible": True, "props": {}, "texts": {"name": "omit"}}]}), encoding="utf-8")


class ReleaseBuilderTests(unittest.TestCase):
    def test_release_is_explicit_whitelist_and_minimal_definitions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_fixture(root)
            (root / "hlboot.dat").write_text("secret game bytecode", encoding="utf-8")
            (root / "research/captures").mkdir()
            (root / "research/captures/raw.ndjson").write_text("secret capture", encoding="utf-8")
            payload = MODULE.source_payload(root)
            self.assertEqual(set(payload), set(MODULE.SOURCE_FILES) | {"release-manifest.json"})
            self.assertNotIn("research/static/unit.json", payload)
            self.assertNotIn("research/static/resource.json", payload)
            self.assertIn("scripts/setup_local.py", payload)
            self.assertNotIn("secret game bytecode", b"".join(payload.values()).decode())

    def test_missing_source_or_cdb_fingerprint_blocks_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_fixture(root)
            profile = root / "research/runtime/build_profile.json"
            profile.write_text(json.dumps({"hlboot_sha256": "a" * 64, "pointer_size": 8}), encoding="utf-8")
            with self.assertRaises(MODULE.BuildError):
                MODULE.source_payload(root)
            (root / "collector/collect.py").unlink()
            with self.assertRaises(MODULE.BuildError):
                MODULE.source_payload(root)

    def test_archive_checksums_members_and_reproducibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {"collector/a.py": b"# source\n", "release-manifest.json": b"{}\n"}
            first = MODULE.write_archive(payload, root / "a.zip")
            second = MODULE.write_archive(payload, root / "b.zip")
            self.assertEqual(first["sha256"], second["sha256"])
            with zipfile.ZipFile(root / "a.zip") as archive:
                self.assertEqual(set(archive.namelist()), set(payload) | {"SHA256SUMS.txt"})
                self.assertIn(b"collector/a.py", archive.read("SHA256SUMS.txt"))
            self.assertTrue((root / "a.zip.sha256").exists())
            self.assertTrue((root / "a.files.sha256").exists())
            with self.assertRaises(MODULE.BuildError):
                MODULE.write_archive(payload, root / "a.zip")

    def test_archive_unsafe_member_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "a.zip"
            with self.assertRaises(MODULE.BuildError):
                MODULE.write_archive({"../escape": b"bad"}, output)
            self.assertFalse(output.exists())

    def test_capture_share_excludes_raw_and_other_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in MODULE.CAPTURE_FILES:
                (root / name).write_text('{"status":"stopped","recording_state":"stopped"}\n', encoding="utf-8")
            (root / "research-raw.ndjson").write_text("SECRET", encoding="utf-8")
            (root / "save.dat").write_text("SECRET", encoding="utf-8")
            payload = MODULE.capture_payload(root)
            self.assertEqual(set(payload), set(MODULE.CAPTURE_FILES) | {"share-manifest.json"})
            self.assertNotIn("SECRET", b"".join(payload.values()).decode("utf-8"))

    def test_capture_share_rejects_private_fields_paths_and_active_recording(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in MODULE.CAPTURE_FILES:
                (root / name).write_text('{"status":"stopped","recording_state":"stopped"}\n', encoding="utf-8")
            for invalid in ({"pid": 123}, {"research_only": True}, {"error": "C:\\Users\\secret"},
                            {"nested": [{"raw_resource_slots": []}]}, {"status": "recording"},
                            {"recording_state": "recording"}, {"status": "stopped"}):
                with self.subTest(invalid=invalid):
                    (root / "manifest.json").write_text(json.dumps(invalid), encoding="utf-8")
                    with self.assertRaises(MODULE.BuildError):
                        MODULE.capture_payload(root)

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("powershell.exe"), "Windows PowerShell launcher check")
    def test_launcher_checks_package_and_runs_only_fixture_collector(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_collector = b"import json,sys\nprint(json.dumps({'fixture_only': True, 'args':sys.argv[1:]}))\n"
            payload = {"collector/collect.py": fake_collector,
                       "collector/Start-Collector.ps1": Path(__file__).with_name("Start-Collector.ps1").read_bytes()}
            payload["release-manifest.json"] = json.dumps({
                "artifact_type": "northgard_collector_source_release",
                "files": [{"path": path, "sha256": hashlib.sha256(raw).hexdigest()} for path, raw in payload.items()]
            }).encode("utf-8")
            MODULE.write_archive(payload, root / "fixture.zip")
            extracted = root / "unpacked"
            with zipfile.ZipFile(root / "fixture.zip") as archive:
                archive.extractall(extracted)
            shared = '12345678-1234-4234-8234-123456789abc'
            variants = ([], ['-SharedMatchId', shared, '-TeamLabel', 'A', '-PlayerSlot', '2', '-DeclaredFormat', '3v3'])
            for flags in variants:
                with self.subTest(flags=flags):
                    result = subprocess.run([shutil.which("powershell.exe"), "-NoProfile", "-File",
                        str(extracted / "collector/Start-Collector.ps1"), "-Seconds", "0",
                        "-OutDir", str(root / "unused-output"), *flags], capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    fixture = next(json.loads(line) for line in result.stdout.splitlines() if line.startswith('{'))
                    self.assertTrue(fixture['fixture_only'])
                    if flags:
                        for key, value in (('--shared-match-id', shared), ('--team-label', 'A'),
                                           ('--player-slot', '2'), ('--declared-format', '3v3')):
                            self.assertEqual(fixture['args'][fixture['args'].index(key)+1], value)
                    else:
                        self.assertNotIn('--shared-match-id', fixture['args'])
                    self.assertFalse((root / "unused-output").exists())


if __name__ == "__main__":
    unittest.main()
