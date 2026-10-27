import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from creative_pipeline.apimart import extract_https_urls, image_payload, video_payload
from creative_pipeline.core import PipelineError, approve, plan, run, validate_campaign


def campaign():
    return {"name": "demo", "assets": [
        {"id": "IMG_A", "kind": "image", "prompt": "first", "depends_on": []},
        {"id": "IMG_B", "kind": "image", "prompt": "second", "depends_on": ["IMG_A"]},
        {"id": "ESC_1", "kind": "video", "prompt": "motion", "depends_on": ["IMG_B"], "duration": 6},
    ]}


class ValidationTests(unittest.TestCase):
    def test_ordered_dag(self):
        data = campaign()
        data["assets"].reverse()
        self.assertEqual([a["id"] for a in validate_campaign(data)], ["IMG_A", "IMG_B", "ESC_1"])

    def test_invalid_campaigns(self):
        cases = []
        for unsafe in ("../secret", "/root", "a/b", "a\\b", ".", "", "a b"):
            c = campaign(); c["assets"][0]["id"] = unsafe; cases.append(c)
        c = campaign(); c["assets"].append(copy.deepcopy(c["assets"][0])); cases.append(c)
        c = campaign(); c["assets"][0]["depends_on"] = ["ESC_1"]; cases.append(c)
        c = campaign(); c["assets"][0]["depends_on"] = ["missing"]; cases.append(c)
        for duration in (0, 5, True, "6", None):
            c = campaign(); c["assets"][2]["duration"] = duration; cases.append(c)
        for deps in ([], ["IMG_A", "IMG_B"], "IMG_B"):
            c = campaign(); c["assets"][2]["depends_on"] = deps; cases.append(c)
        for field, value in (("prompt", ""), ("kind", "audio"), ("depends_on", ["IMG_A", "IMG_A"])):
            c = campaign(); c["assets"][1][field] = value; cases.append(c)
        c = campaign(); c["unknown"] = 1; cases.append(c)
        c = campaign(); c["assets"][0]["duration"] = 4; cases.append(c)
        cases.extend([None, [], {"name": "demo", "assets": []}])
        for c in cases:
            with self.subTest(c=c), self.assertRaises(PipelineError):
                validate_campaign(c)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "run"

    def test_plan_only_and_changed_plan_rejected(self):
        result = plan(campaign(), self.out)
        self.assertEqual(result["order"], ["IMG_A", "IMG_B", "ESC_1"])
        self.assertTrue((self.out / "plan.json").exists())
        self.assertFalse((self.out / "manifest.json").exists())
        self.assertFalse((self.out / "assets").exists())
        changed = campaign(); changed["assets"][0]["prompt"] = "changed"
        with self.assertRaises(PipelineError):
            run(changed, self.out)

    def test_gating_resume_and_explicit_approval(self):
        with patch("creative_pipeline.core.MockProvider.submit", wraps=None) as submit:
            submit.side_effect = lambda asset, deps: {"mock": True, "asset_id": asset["id"], "media_generated": False}
            first = run(campaign(), self.out)
            self.assertEqual(submit.call_count, 1)
            self.assertEqual(first["assets"]["IMG_B"]["status"], "pending")
            run(campaign(), self.out)
            self.assertEqual(submit.call_count, 1)
            with self.assertRaises(PipelineError):
                approve(self.out, "IMG_B", "reviewer")
            with self.assertRaises(PipelineError):
                approve(self.out, "IMG_A", " ")
            approve(self.out, "IMG_A", "demo-reviewer")
            run(campaign(), self.out)
            self.assertEqual(submit.call_count, 2)
            approve(self.out, "IMG_B", "demo-reviewer")
            final = run(campaign(), self.out)
            self.assertEqual(submit.call_count, 3)
            run(campaign(), self.out)
            self.assertEqual(submit.call_count, 3)
            self.assertEqual(final["assets"]["IMG_A"]["approval"]["reviewer"], "demo-reviewer")
            changed = campaign(); changed["assets"][0]["prompt"] = "new paid prompt"
            with self.assertRaises(PipelineError):
                run(changed, self.out)
            self.assertEqual(submit.call_count, 3)

    def test_mock_records_are_json_not_media(self):
        run(campaign(), self.out)
        record = json.loads((self.out / "assets" / "IMG_A.json").read_text())
        self.assertTrue(record["mock"])
        self.assertFalse(record["media_generated"])
        self.assertEqual(record["record_type"], "mock_asset_record")
        self.assertEqual(list((self.out / "assets").glob("*.mp4")), [])

    def test_uncertain_submission_never_retried(self):
        with patch("creative_pipeline.core.MockProvider.submit", side_effect=RuntimeError("crash")):
            with self.assertRaises(PipelineError):
                run(campaign(), self.out)
        with patch("creative_pipeline.core.MockProvider.submit") as submit:
            with self.assertRaises(PipelineError):
                run(campaign(), self.out)
            submit.assert_not_called()

    def test_unknown_provider_and_concurrent_lock_rejected(self):
        with self.assertRaises(PipelineError):
            run(campaign(), self.out, provider="apimart")
        self.out.mkdir(exist_ok=True)
        (self.out / ".pipeline.lock").write_text("busy")
        with self.assertRaises(PipelineError):
            run(campaign(), self.out)

    def test_missing_completed_record_fails_closed(self):
        run(campaign(), self.out)
        (self.out / "assets" / "IMG_A.json").unlink()
        with patch("creative_pipeline.core.MockProvider.submit") as submit:
            with self.assertRaises(PipelineError):
                run(campaign(), self.out)
            submit.assert_not_called()
        with self.assertRaises(PipelineError):
            approve(self.out, "IMG_A", "reviewer")

    def test_atomic_replace_failure_preserves_manifest(self):
        run(campaign(), self.out)
        before = (self.out / "manifest.json").read_bytes()
        with patch("creative_pipeline.core.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                approve(self.out, "IMG_A", "reviewer")
        self.assertEqual((self.out / "manifest.json").read_bytes(), before)
        self.assertEqual(list(self.out.glob(".pending-*")), [])
        self.assertFalse((self.out / ".pipeline.lock").exists())

    def test_tampered_state_rejected(self):
        run(campaign(), self.out)
        path = self.out / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["assets"]["IMG_B"]["fingerprint"] = "changed"
        path.write_text(json.dumps(manifest))
        with patch("creative_pipeline.core.MockProvider.submit") as submit:
            with self.assertRaises(PipelineError):
                run(campaign(), self.out)
            submit.assert_not_called()

    def test_cli(self):
        source = Path(self.tmp.name) / "campaign.json"
        source.write_text(json.dumps(campaign()))
        def cli(*args):
            return subprocess.run([sys.executable, "-m", "creative_pipeline", *map(str, args)], capture_output=True, text=True)
        self.assertEqual(cli("plan", source, "--out", self.out).returncode, 0)
        self.assertEqual(cli("run", source, "--out", self.out, "--provider", "mock").returncode, 0)
        self.assertEqual(cli("approve", self.out, "IMG_A", "--reviewer", "demo-reviewer").returncode, 0)
        self.assertNotEqual(cli("run", source, "--out", self.out, "--provider", "apimart").returncode, 0)


class PayloadTests(unittest.TestCase):
    def test_image(self):
        self.assertEqual(image_payload("portrait"), {
            "model": "gpt-image-2-ext", "prompt": "portrait", "n": 1,
            "size": "9:16", "resolution": "2k", "official_fallback": False})

    def test_video(self):
        for duration in (4, 6, 8, 10):
            self.assertEqual(video_payload("move", ["https://example.test/image.png"], duration), {
                "model": "Omni-Flash-Ext", "prompt": "move", "generation_type": "frame",
                "image_urls": ["https://example.test/image.png"], "duration": duration,
                "aspect_ratio": "9:16", "resolution": "720p"})
        for refs in ([], ["https://a.test", "https://b.test"], ["http://a.test"], ["https://"], "https://a.test"):
            with self.assertRaises(ValueError):
                video_payload("move", refs, 6)
        for duration in (5, True, "6"):
            with self.assertRaises(ValueError):
                video_payload("move", ["https://a.test"], duration)

    def test_nested_urls(self):
        data = {"result": [{"video_urls": ["https://cdn.test/a.mp4", "http://unsafe.test"]},
                           {"nested": {"url": "https://cdn.test/b.png"}},
                           "https://cdn.test/a.mp4", "https://", "not a URL"]}
        self.assertEqual(extract_https_urls(data), ["https://cdn.test/a.mp4", "https://cdn.test/b.png"])
        self.assertEqual(extract_https_urls(None), [])


if __name__ == "__main__":
    unittest.main()
