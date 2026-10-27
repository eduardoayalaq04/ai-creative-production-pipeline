"""Regressions for case-insensitive IDs, approval integrity and HTTPS ports."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from creative_pipeline.apimart import extract_https_urls, video_payload
from creative_pipeline.core import (
    PipelineError, _validate_manifest, approve, run, validate_campaign,
)
from tests.test_pipeline import campaign


class AssetIdRegressionTests(unittest.TestCase):
    def test_casefold_duplicate_ids_rejected(self):
        for duplicate in ("img_a", "Img_A", "IMG_A"):
            with self.subTest(duplicate=duplicate):
                data = campaign()
                asset = copy.deepcopy(data["assets"][0])
                asset["id"] = duplicate
                data["assets"].append(asset)
                with self.assertRaisesRegex(PipelineError, "Duplicate asset id"):
                    validate_campaign(data)

    def test_distinct_ids_preserve_case(self):
        data = campaign()
        data["assets"].append({"id": "img_c", "kind": "image",
                               "prompt": "third", "depends_on": []})
        self.assertEqual([a["id"] for a in validate_campaign(data)],
                         ["IMG_A", "img_c", "IMG_B", "ESC_1"])


class ApprovalRegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.out = Path(temporary.name) / "run"
        self.campaign = campaign()
        self.ordered = validate_campaign(self.campaign)
        self.manifest = run(self.campaign, self.out)

    def test_noncompleted_approval_must_be_null(self):
        for status in ("pending", "submitting"):
            for approval in (False, 0, "", {}, [], {"reviewer": "human", "at": "2026-10-26T19:18:00+00:00"}):
                with self.subTest(status=status, approval=approval):
                    manifest = copy.deepcopy(self.manifest)
                    state = manifest["assets"]["IMG_B"]
                    state.update(status=status, approval=approval)
                    with self.assertRaisesRegex(PipelineError, "approval"):
                        _validate_manifest(manifest, self.campaign, self.ordered)

    def test_missing_approval_rejected(self):
        for aid in ("IMG_A", "IMG_B"):
            with self.subTest(asset=aid):
                manifest = copy.deepcopy(self.manifest)
                del manifest["assets"][aid]["approval"]
                with self.assertRaisesRegex(PipelineError, "approval"):
                    _validate_manifest(manifest, self.campaign, self.ordered)

    def test_malformed_completed_approval_cannot_bypass_gate_or_approve(self):
        valid = {"reviewer": "human", "at": "2026-10-26T19:18:00+00:00"}
        cases = [True, False, 1, 0, "approved", "", [], {},
                 {"reviewer": "human"}, {"at": valid["at"]},
                 dict(valid, extra=True)]
        for reviewer in (None, False, 1, "", " \t"):
            cases.append(dict(valid, reviewer=reviewer))
        for at in (None, False, 1, "", "not-a-date", "2026-02-30T12:00:00+00:00",
                   "2026-10-26", "2026-10-26T19:18:00", "2026-10-26T19:18:00+25:00"):
            cases.append(dict(valid, at=at))
        path = self.out / "manifest.json"
        for approval in cases:
            with self.subTest(approval=approval):
                manifest = copy.deepcopy(self.manifest)
                manifest["assets"]["IMG_A"]["approval"] = approval
                path.write_text(json.dumps(manifest), encoding="utf-8")
                before = path.read_bytes()
                with patch("creative_pipeline.core.MockProvider.submit", return_value={}) as submit:
                    with self.assertRaisesRegex(PipelineError, "approval"):
                        run(self.campaign, self.out)
                    submit.assert_not_called()
                with self.assertRaisesRegex(PipelineError, "approval"):
                    approve(self.out, "IMG_A", "human")
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse((self.out / "assets" / "IMG_B.json").exists())

    def test_valid_completed_approvals_and_null_accepted(self):
        for approval in (None,
                         {"reviewer": "human", "at": "2026-10-26T19:18:00+00:00"},
                         {"reviewer": "human", "at": "2026-10-26T19:18:00-03:30"}):
            with self.subTest(approval=approval):
                manifest = copy.deepcopy(self.manifest)
                manifest["assets"]["IMG_A"]["approval"] = approval
                _validate_manifest(manifest, self.campaign, self.ordered)


class HttpsPortRegressionTests(unittest.TestCase):
    def test_invalid_ports_rejected_by_payload(self):
        for port in ("abc", "65536", "-1", "443:80"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                video_payload("move", ["https://cdn.test:" + port + "/image.png"], 6)

    def test_invalid_ports_excluded_from_extracted_urls(self):
        for port in ("abc", "65536", "-1", "443:80"):
            with self.subTest(port=port):
                self.assertEqual(extract_https_urls({"url": "https://cdn.test:" + port + "/image.png"}), [])

    def test_valid_explicit_ports_and_default_port_accepted(self):
        for authority in ("cdn.test", "cdn.test:443", "cdn.test:65535", "[::1]:443"):
            with self.subTest(authority=authority):
                url = "https://" + authority + "/image.png"
                self.assertEqual(video_payload("move", [url], 6)["image_urls"], [url])
                self.assertEqual(extract_https_urls({"url": url}), [url])


if __name__ == "__main__":
    unittest.main()
