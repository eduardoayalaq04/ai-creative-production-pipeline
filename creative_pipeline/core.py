"""Local reference workflow. No network calls or media generation."""
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class PipelineError(ValueError):
    """Invalid input or unsafe workflow transition."""


def validate_campaign(data):
    if not isinstance(data, dict) or set(data) != {"name", "assets"}:
        raise PipelineError("Campaign requires exactly name and assets")
    if not isinstance(data["name"], str) or not data["name"].strip():
        raise PipelineError("Campaign name must be nonempty")
    if not isinstance(data["assets"], list) or not data["assets"]:
        raise PipelineError("Campaign assets must be a nonempty list")
    indexed = {}
    folded_ids = set()
    for asset in data["assets"]:
        if not isinstance(asset, dict):
            raise PipelineError("Each asset must be an object")
        fields = {"id", "kind", "prompt", "depends_on"}
        if asset.get("kind") == "video":
            fields.add("duration")
        if set(asset) != fields:
            raise PipelineError("Unexpected or missing asset fields")
        aid = asset["id"]
        if not isinstance(aid, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", aid):
            raise PipelineError("Asset id must be a safe identifier (1-64 characters)")
        if aid.casefold() in folded_ids:
            raise PipelineError("Duplicate asset id: " + aid)
        folded_ids.add(aid.casefold())
        if asset["kind"] not in ("image", "video"):
            raise PipelineError("Asset kind must be image or video")
        if not isinstance(asset["prompt"], str) or not asset["prompt"].strip():
            raise PipelineError("Asset prompt must be nonempty")
        deps = asset["depends_on"]
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps) or len(deps) != len(set(deps)):
            raise PipelineError("depends_on must be a list of unique identifiers")
        if asset["kind"] == "video":
            if len(deps) != 1 or type(asset["duration"]) is not int or asset["duration"] not in (4, 6, 8, 10):
                raise PipelineError("Video requires one image dependency and duration 4/6/8/10")
        indexed[aid] = asset
    for asset in indexed.values():
        if any(d not in indexed for d in asset["depends_on"]):
            raise PipelineError("Unknown dependency")
        if asset["kind"] == "video" and indexed[asset["depends_on"][0]]["kind"] != "image":
            raise PipelineError("Video reference dependency must be an image")
    ordered = []
    remaining = dict(indexed)
    done = set()
    while remaining:
        ready = [aid for aid, a in remaining.items() if set(a["depends_on"]) <= done]
        if not ready:
            raise PipelineError("Dependency cycle detected")
        for aid in ready:
            ordered.append(remaining.pop(aid))
            done.add(aid)
    return ordered


def _fingerprint(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read(path):
    try:
        with Path(path).open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError) as exc:
        raise PipelineError("Cannot read JSON: " + str(path)) from exc


@contextmanager
def _locked(out):
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".pipeline.lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise PipelineError("Run is locked; verify no process is active before removing .pipeline.lock") from exc
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink()


def _check_existing(out, fingerprint):
    for filename in ("plan.json", "manifest.json"):
        path = out / filename
        if path.exists():
            data = _read(path)
            if not isinstance(data, dict) or data.get("fingerprint") != fingerprint:
                raise PipelineError("Campaign changed or invalid run metadata; use a new output directory")


def plan(campaign, out):
    """Write only a validated topological plan; never submit jobs."""
    ordered = validate_campaign(campaign)
    out = Path(out)
    fingerprint = _fingerprint(campaign)
    with _locked(out):
        _check_existing(out, fingerprint)
        result = {"schema_version": 1, "fingerprint": fingerprint,
                  "name": campaign["name"], "order": [a["id"] for a in ordered], "assets": ordered}
        _atomic_json(out / "plan.json", result)
    return result


class MockProvider:
    """Produces JSON metadata only, never images, videos, or invented media URLs."""
    def submit(self, asset, dependencies):
        return {"record_type": "mock_asset_record", "mock": True, "media_generated": False,
                "provider": "mock", "asset_id": asset["id"], "kind": asset["kind"],
                "prompt": asset["prompt"], "duration": asset.get("duration"),
                "approved_dependencies": list(dependencies),
                "notice": "MOCK ONLY: no media generated and no API called"}


def _validate_manifest(manifest, campaign, ordered):
    if (manifest.get("schema_version") != 1 or manifest.get("provider") != "mock"
            or manifest.get("campaign") != campaign
            or not isinstance(manifest.get("assets"), dict)
            or set(manifest["assets"]) != {a["id"] for a in ordered}):
        raise PipelineError("Invalid manifest; refusing to submit")
    for asset in ordered:
        state = manifest["assets"][asset["id"]]
        if (not isinstance(state, dict) or state.get("fingerprint") != _fingerprint(asset)
                or state.get("status") not in ("pending", "completed", "submitting")):
            raise PipelineError("Invalid asset state; refusing to submit")
        if "approval" not in state:
            raise PipelineError("Missing approval; refusing to submit")
        approval = state["approval"]
        if approval is not None:
            if (state["status"] != "completed" or not isinstance(approval, dict)
                    or set(approval) != {"reviewer", "at"}
                    or not isinstance(approval["reviewer"], str)
                    or not approval["reviewer"].strip()
                    or not isinstance(approval["at"], str)):
                raise PipelineError("Invalid approval; refusing to submit")
            try:
                timestamp = datetime.fromisoformat(approval["at"].replace("Z", "+00:00"))
            except ValueError as exc:
                raise PipelineError("Invalid approval timestamp; refusing to submit") from exc
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise PipelineError("Invalid approval timestamp: timezone required")
        if state["status"] == "submitting":
            raise PipelineError("Uncertain prior submission; manual reconciliation required, never automatically retried")


def run(campaign, out, provider="mock"):
    """Resume a mock run, stopping each dependency edge until explicit approval."""
    if provider != "mock":
        raise PipelineError("Only mock is executable; APIMart helpers are pure request builders")
    ordered = validate_campaign(campaign)
    fingerprint = _fingerprint(campaign)
    out = Path(out)
    with _locked(out):
        _check_existing(out, fingerprint)
        path = out / "manifest.json"
        if path.exists():
            manifest = _read(path)
        else:
            manifest = {"schema_version": 1, "fingerprint": fingerprint, "provider": provider,
                        "campaign": campaign, "assets": {
                            a["id"]: {"status": "pending", "fingerprint": _fingerprint(a), "approval": None}
                            for a in ordered}}
        _validate_manifest(manifest, campaign, ordered)
        # Missing records are not silently regenerated, even for this mock provider.
        for aid, state in manifest["assets"].items():
            if state["status"] == "completed" and not (out / "assets" / (aid + ".json")).is_file():
                raise PipelineError("Completed asset record missing; refusing to regenerate: " + aid)
        _atomic_json(path, manifest)
        for asset in ordered:
            state = manifest["assets"][asset["id"]]
            if state["status"] == "completed":
                continue
            deps = asset["depends_on"]
            if any(manifest["assets"][d]["status"] != "completed" or not manifest["assets"][d].get("approval") for d in deps):
                continue
            state["status"] = "submitting"
            _atomic_json(path, manifest)
            try:
                record = MockProvider().submit(asset, deps)
                _atomic_json(out / "assets" / (asset["id"] + ".json"), record)
            except Exception as exc:
                raise PipelineError("Submission interrupted; refusing automatic retry") from exc
            state["status"] = "completed"
            state["record"] = "assets/" + asset["id"] + ".json"
            _atomic_json(path, manifest)
    return manifest


def approve(out, asset_id, reviewer):
    """Record a named human approval of an existing completed mock record."""
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise PipelineError("A nonempty reviewer is required")
    out = Path(out)
    with _locked(out):
        path = out / "manifest.json"
        manifest = _read(path)
        if not isinstance(manifest, dict):
            raise PipelineError("Invalid manifest")
        campaign = manifest.get("campaign")
        ordered = validate_campaign(campaign)
        _check_existing(out, _fingerprint(campaign))
        _validate_manifest(manifest, campaign, ordered)
        state = manifest["assets"].get(asset_id)
        if not state or state["status"] != "completed":
            raise PipelineError("Only completed assets may be approved")
        if not (out / "assets" / (asset_id + ".json")).is_file():
            raise PipelineError("Cannot approve a missing record")
        if not state.get("approval"):
            state["approval"] = {"reviewer": reviewer.strip(), "at": datetime.now(timezone.utc).isoformat()}
            _atomic_json(path, manifest)
    return manifest
