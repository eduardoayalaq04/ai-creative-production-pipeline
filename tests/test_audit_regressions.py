"""Fail-closed audit regressions: local paths, records, DAG and resource limits."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from creative_pipeline.apimart import extract_https_urls
from creative_pipeline.core import (
    MAX_ASSETS, MAX_DEPTH, MAX_JSON_BYTES, MAX_NODES, PipelineError,
    _atomic_json, _fingerprint, _read, approve, plan, run, validate_campaign,
)
from tests.test_pipeline import campaign


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.out = self.root / "run"

    def assert_closed(self):
        before = (self.out / "manifest.json").read_bytes()
        with patch("creative_pipeline.core.MockProvider.submit") as submit:
            for action in (lambda: run(campaign(), self.out),
                           lambda: approve(self.out, "IMG_A", "human")):
                with self.assertRaises(PipelineError):
                    action()
            submit.assert_not_called()
        self.assertEqual((self.out / "manifest.json").read_bytes(), before)
        self.assertFalse((self.out / ".pipeline.lock").exists())

    def test_symlink_output_and_ancestor(self):
        target = self.root / "outside"
        target.mkdir()
        link = self.root / "linked"
        link.symlink_to(target, target_is_directory=True)
        for out in (link, link / "child", link / "missing" / "child"):
            for action in (plan, run):
                with self.subTest(out=out, action=action), self.assertRaises(PipelineError):
                    action(campaign(), out)
        self.assertEqual(list(target.iterdir()), [])

    def test_metadata_assets_and_lock_symlinks_including_dangling(self):
        self.out.mkdir()
        for name in ("assets", "plan.json", "manifest.json", ".pipeline.lock"):
            for dangling in (False, True):
                target = self.root / ("missing" if dangling else "outside")
                if not dangling:
                    target.write_text("untouched")
                link = self.out / name
                link.symlink_to(target)
                for action in (plan, run):
                    with self.subTest(name=name, dangling=dangling), self.assertRaises(PipelineError):
                        action(campaign(), self.out)
                with self.assertRaises(PipelineError):
                    approve(self.out, "IMG_A", "human")
                link.unlink()
                if not dangling:
                    self.assertEqual(target.read_text(), "untouched")
                else:
                    self.assertFalse(target.exists())

    def test_completed_and_pending_record_symlinks(self):
        run(campaign(), self.out)
        for aid in ("IMG_A", "IMG_B"):
            path = self.out / "assets" / (aid + ".json")
            original = path.read_bytes() if path.exists() else None
            if path.exists():
                path.unlink()
            target = self.root / "outside.json"
            target.write_text("untouched")
            path.symlink_to(target)
            self.assert_closed()
            self.assertEqual(target.read_text(), "untouched")
            path.unlink()
            if original is not None:
                path.write_bytes(original)

    def test_input_and_atomic_write_reject_symlink_parents(self):
        target = self.root / "outside"
        target.mkdir()
        (target / "data.json").write_text("{}")
        link = self.root / "linked"
        link.symlink_to(target, target_is_directory=True)
        for path in (link / "data.json", link / "new.json"):
            with self.assertRaises(PipelineError):
                _read(path)
            with self.assertRaises(PipelineError):
                _atomic_json(path, {})
        self.assertEqual((target / "data.json").read_text(), "{}")
        self.assertFalse((target / "new.json").exists())

    def test_corrupt_records_even_with_updated_state_hash(self):
        manifest = run(campaign(), self.out)
        record_path = self.out / "assets" / "IMG_A.json"
        expected = json.loads(record_path.read_text())
        variants = ["", "not json", "[]", "{}", "null"]
        for key, value in (("prompt", "tampered"), ("mock", 1),
                           ("approved_dependencies", ["IMG_B"]), ("extra", True)):
            record = dict(expected, **{key: value})
            variants.append(json.dumps(record))
        for raw in variants:
            with self.subTest(raw=raw):
                record_path.write_text(raw)
                self.assert_closed()
        record = dict(expected, prompt="changed")
        record_path.write_text(json.dumps(record))
        manifest["assets"]["IMG_A"]["record_sha256"] = _fingerprint(record)
        (self.out / "manifest.json").write_text(json.dumps(manifest))
        self.assert_closed()

    def test_record_hash_and_approval_binding_and_path(self):
        run(campaign(), self.out)
        original = approve(self.out, "IMG_A", "human")
        mutations = [("record_sha256", "0" * 64), ("record", "../outside.json")]
        for key, value in mutations:
            manifest = copy.deepcopy(original)
            manifest["assets"]["IMG_A"][key] = value
            (self.out / "manifest.json").write_text(json.dumps(manifest))
            self.assert_closed()
        manifest = copy.deepcopy(original)
        manifest["assets"]["IMG_A"]["approval"]["record_sha256"] = "0" * 64
        (self.out / "manifest.json").write_text(json.dumps(manifest))
        self.assert_closed()

    def test_completed_child_requires_approved_completed_parent(self):
        run(campaign(), self.out)
        approve(self.out, "IMG_A", "human")
        original = run(campaign(), self.out)
        for pending in (False, True):
            manifest = copy.deepcopy(original)
            parent = manifest["assets"]["IMG_A"]
            parent["approval"] = None
            if pending:
                parent["status"] = "pending"
                del parent["record"]
                del parent["record_sha256"]
            (self.out / "manifest.json").write_text(json.dumps(manifest))
            self.assert_closed()

    def test_utf8_and_json_limits_cli_no_traceback(self):
        source = self.root / "input.json"
        samples = [b'\xff', b'{"name":"\\ud800","assets":[]}',
                   b'[' * 2000 + b'0' + b']' * 2000,
                   b' ' * (MAX_JSON_BYTES + 1)]
        for raw in samples:
            source.write_bytes(raw)
            with self.assertRaises(PipelineError):
                _read(source)
            result = subprocess.run([sys.executable, "-m", "creative_pipeline", "plan",
                                     str(source), "--out", str(self.out)], capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn(b"Traceback", result.stderr)
            self.assertFalse(self.out.exists())

    def test_programmatic_limits_and_unicode(self):
        for field in ("name", "prompt"):
            data = campaign()
            if field == "name":
                data[field] = "\udfff"
            else:
                data["assets"][0][field] = "\udfff"
            with self.assertRaises(PipelineError):
                run(data, self.out)
        with self.assertRaises(PipelineError):
            approve(self.out, "IMG_A", "\udfff")
        data = campaign()
        data["assets"] = [dict(data["assets"][0], id="A" + str(i)) for i in range(MAX_ASSETS + 1)]
        with self.assertRaisesRegex(PipelineError, "Too many assets"):
            validate_campaign(data)
        data["assets"].pop()
        self.assertEqual(len(validate_campaign(data)), MAX_ASSETS)
        deep = 0
        for _ in range(MAX_DEPTH + 1):
            deep = [deep]
        cycle = []; cycle.append(cycle)
        for value in (deep, cycle, [0] * (MAX_NODES + 1), "x" * (MAX_JSON_BYTES + 1)):
            with self.assertRaises(PipelineError):
                _atomic_json(self.root / "data.json", value)
        self.assertFalse((self.root / "data.json").exists())

    def test_url_limits_cycles_and_shared_containers(self):
        deep = "https://cdn.test/a"
        for _ in range(MAX_DEPTH + 1):
            deep = [deep]
        cycle = []; cycle.append(cycle)
        mapping = {}; mapping["self"] = mapping
        for value in (deep, cycle, mapping, [0] * (MAX_NODES + 1),
                      "x" * (MAX_JSON_BYTES + 1), "\ud800"):
            with self.assertRaises(ValueError):
                extract_https_urls(value)
        shared = ["https://cdn.test/a", "https://cdn.test/b"]
        self.assertEqual(extract_https_urls([shared, shared]), shared)

    def test_manifest_schema_and_malformed_states_fail_closed(self):
        original = run(campaign(), self.out)
        variants = [None, [], {}, dict(original, schema_version=1)]
        for bad_state in (None, [], "completed", {"status": "completed"}):
            manifest = copy.deepcopy(original)
            manifest["assets"]["IMG_A"] = bad_state
            variants.append(manifest)
        for manifest in variants:
            with self.subTest(manifest=manifest):
                (self.out / "manifest.json").write_text(json.dumps(manifest))
                self.assert_closed()

    def test_completed_manifest_size_preflight_never_submits(self):
        data = {"name": "x" * 2096674, "assets": [
            {"id": "A", "kind": "image", "prompt": "p", "depends_on": []}]}
        validate_campaign(data)
        with patch("creative_pipeline.core.MockProvider.submit") as submit:
            for _ in range(2):
                with self.assertRaisesRegex(PipelineError, "size limit"):
                    run(data, self.out)
                manifest = _read(self.out / "manifest.json")
                self.assertEqual(manifest["assets"]["A"]["status"], "pending")
                self.assertFalse((self.out / "assets").exists())
                self.assertFalse((self.out / ".pipeline.lock").exists())
            submit.assert_not_called()
        # The submitting state fits exactly; only the projected completed state overflows.
        manifest["assets"]["A"]["status"] = "submitting"
        raw = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        self.assertEqual(len(raw.encode("utf-8")), MAX_JSON_BYTES)

    def test_oversized_approval_leaves_manifest_unchanged(self):
        run(campaign(), self.out)
        before = (self.out / "manifest.json").read_bytes()
        with patch("creative_pipeline.core.MockProvider.submit") as submit:
            with self.assertRaises(PipelineError):
                approve(self.out, "IMG_A", "x" * MAX_JSON_BYTES)
            submit.assert_not_called()
        self.assertEqual((self.out / "manifest.json").read_bytes(), before)
        self.assertIsNone(_read(self.out / "manifest.json")["assets"]["IMG_A"]["approval"])
        self.assertFalse((self.out / ".pipeline.lock").exists())

    def test_rejects_unexpected_provider_record_and_never_retries(self):
        with patch("creative_pipeline.core.MockProvider.submit", return_value={"mock": True}):
            with self.assertRaises(PipelineError):
                run(campaign(), self.out)
        manifest = _read(self.out / "manifest.json")
        self.assertEqual(manifest["assets"]["IMG_A"]["status"], "submitting")
        self.assertFalse((self.out / "assets" / "IMG_A.json").exists())
        self.assert_closed()

    def test_record_formatting_does_not_invalidate_approval(self):
        run(campaign(), self.out)
        approved = approve(self.out, "IMG_A", "human")
        path = self.out / "assets" / "IMG_A.json"
        path.write_text(json.dumps(json.loads(path.read_text()), sort_keys=True))
        resumed = run(campaign(), self.out)
        self.assertEqual(resumed["assets"]["IMG_A"]["approval"], approved["assets"]["IMG_A"]["approval"])
        self.assertEqual(resumed["assets"]["IMG_B"]["status"], "completed")
