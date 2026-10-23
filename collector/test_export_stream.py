"""Bounded export tests, including a capture larger than the old 256 MiB cap."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location("build_release_stream", Path(__file__).with_name("build_release.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fixture(root):
    root.mkdir(parents=True, exist_ok=True)
    for name in MODULE.CAPTURE_FILES:
        value = {"status": "ok", "value": 1}
        if name == "manifest.json":
            value = {"recording_state": "stopped", "collector_version": "0.1.0-alpha"}
        (root / name).write_text(json.dumps(value) + "\n", encoding="utf-8")
    (root / "research-raw.ndjson").write_text('{"account_id":"private"}\n', encoding="utf-8")


class StreamExportTests(unittest.TestCase):
    def test_streaming_whitelist_checksums_versions_and_reproducibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "capture"
            fixture(capture)
            # The streaming path must not call Path.read_bytes, including for
            # hashing the final ZIP. Small test-side reads happen afterwards.
            with patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read")):
                first = MODULE.write_capture_archive(capture, root / "a.zip")
                second = MODULE.write_capture_archive(capture, root / "b.zip")
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertTrue(first["streaming_export"])
            with zipfile.ZipFile(root / "a.zip") as archive:
                self.assertEqual(set(archive.namelist()), set(MODULE.CAPTURE_FILES) | {"share-manifest.json", "SHA256SUMS.txt"})
                manifest = json.loads(archive.read("share-manifest.json"))
                self.assertEqual(manifest["source_collector_version"], "0.1.0-alpha")
                self.assertEqual(manifest["collector_version"], "0.1.0-alpha")
                self.assertEqual(manifest["exporter_version"], MODULE.VERSION)
                self.assertEqual(archive.read("manifest.json"), (capture / "manifest.json").read_bytes())
                for row in manifest["files"]:
                    raw = archive.read(row["path"])
                    self.assertEqual(row["sha256"], hashlib.sha256(raw).hexdigest())
                    self.assertEqual(row["size_bytes"], len(raw))
                sums = dict(line.split("  ", 1)[::-1] for line in archive.read("SHA256SUMS.txt").decode().splitlines())
                for name, digest in sums.items():
                    self.assertEqual(digest, hashlib.sha256(archive.read(name)).hexdigest())
            self.assertEqual(first["sha256"], hashlib.sha256((root / "a.zip").read_bytes()).hexdigest())
            self.assertFalse(list(root.glob("*.tmp")))
            with self.assertRaises(MODULE.BuildError):
                MODULE.write_capture_archive(capture, root / "a.zip")

    def test_real_file_larger_than_256_mib_is_streamed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "capture"
            fixture(capture)
            big = capture / "player-observations.ndjson"
            # Blank NDJSON records are permitted, but bounded individually.
            # 257 MiB of padding tests real disk/ZIP streaming without millions
            # of tiny JSON parser calls or a huge in-memory fixture.
            block = b" " * (1024 ** 2) + b"\n"
            with big.open("wb") as handle:
                for _ in range(257):
                    handle.write(block)
                handle.write(b'{"status":"ok"}\n')
            self.assertGreater(big.stat().st_size, 256 * 1024 ** 2)
            with patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read")):
                result = MODULE.write_capture_archive(capture, root / "large.zip")
            with zipfile.ZipFile(root / "large.zip") as archive:
                self.assertEqual(archive.getinfo(big.name).file_size, big.stat().st_size)
                self.assertEqual(len(archive.namelist()), 8)
            self.assertGreater(result["source_bytes"], 256 * 1024 ** 2)

    def test_private_nonfinite_malformed_and_deep_records_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "capture"
            fixture(capture)
            bad_records = [b'{"account_id":"secret"}\n', b'{"research_only":true}\n',
                           b'{"nested":{"pid":123}}\n', b'{"raw_resource_slots":[]}\n',
                           b'{"error":"C:\\\\Users\\\\secret"}\n',
                           b'{"v":NaN}\n', b'{"v":Infinity}\n', b'{"v":1e400}\n',
                           b'{broken}\n', b"[" * 1100 + b"0" + b"]" * 1100]
            for index, raw in enumerate(bad_records):
                with self.subTest(index=index):
                    (capture / "player-observations.ndjson").write_bytes(raw)
                    output = root / (str(index) + ".zip")
                    with self.assertRaises(MODULE.BuildError):
                        MODULE.write_capture_archive(capture, output)
                    self.assertFalse(output.exists())
                    self.assertFalse(list(root.glob("*.tmp")))

    def test_active_manifest_refused_before_large_file_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "capture"
            fixture(capture)
            (capture / "manifest.json").write_text('{"recording_state":"recording"}', encoding="utf-8")
            original = MODULE._stream_capture_member
            calls = []
            def tracked(path, relative, *args, **kwargs):
                calls.append(relative)
                return original(path, relative, *args, **kwargs)
            with patch.object(MODULE, "_stream_capture_member", side_effect=tracked):
                with self.assertRaises(MODULE.BuildError):
                    MODULE.write_capture_archive(capture, root / "active.zip")
            self.assertEqual(calls, ["manifest.json"])

    def test_explicit_record_file_and_total_limits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "capture"
            fixture(capture)
            for options in ({"max_file_bytes": 1}, {"max_total_bytes": 1}, {"max_record_bytes": 8}):
                with self.subTest(options=options):
                    with self.assertRaises(MODULE.BuildError):
                        MODULE.write_capture_archive(capture, root / "limit.zip", **options)
            (capture / "player-observations.ndjson").write_text(json.dumps({"v": "x" * 300}) + "\n")
            with self.assertRaises(MODULE.BuildError):
                MODULE.write_capture_archive(capture, root / "line.zip", max_record_bytes=128)

    def test_mutation_between_validation_and_copy_leaves_no_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "capture"
            fixture(capture)
            original = MODULE._stream_capture_member
            def mutate(path, relative, *args, **kwargs):
                if not kwargs.get("validate", True):
                    path.write_text('{"status":"changed"}\n')
                return original(path, relative, *args, **kwargs)
            with patch.object(MODULE, "_stream_capture_member", side_effect=mutate):
                with self.assertRaises(MODULE.BuildError):
                    MODULE.write_capture_archive(capture, root / "changed.zip")
            self.assertEqual([p.name for p in root.iterdir()], ["capture"])

    def test_mutation_after_copy_and_interrupt_both_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "capture"
            fixture(capture)
            original = MODULE._hash_file
            def mutate(path):
                (capture / "health.ndjson").write_text('{"status":"changed"}\n')
                return original(path)
            with patch.object(MODULE, "_hash_file", side_effect=mutate):
                with self.assertRaises(MODULE.BuildError):
                    MODULE.write_capture_archive(capture, root / "changed.zip")
            with patch.object(MODULE, "_hash_file", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    MODULE.write_capture_archive(capture, root / "interrupted.zip")
            self.assertEqual([p.name for p in root.iterdir()], ["capture"])

    def test_legacy_payload_preserves_source_version_or_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root)
            manifest = json.loads(MODULE.capture_payload(root)["share-manifest.json"])
            self.assertEqual(manifest["source_collector_version"], "0.1.0-alpha")
            (root / "manifest.json").write_text('{"recording_state":"stopped"}')
            manifest = json.loads(MODULE.capture_payload(root)["share-manifest.json"])
            self.assertEqual(manifest["collector_version"], "unknown")
            self.assertEqual(manifest["exporter_version"], MODULE.VERSION)


if __name__ == "__main__":
    unittest.main()
