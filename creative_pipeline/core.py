"""Local reference workflow. No network calls or media generation.

Paths reject symlinks in every component, including existing parents. The tree
must still be trusted: these checks are not protection against a concurrent
adversary swapping paths (TOCTOU). Atomic replacement prevents partial JSON;
file fsync alone does not promise power-loss durability of directory entries.
"""
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


MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_ASSETS = 256
MAX_DEPTH = 32
MAX_NODES = 100000


def _safe_path(path):
    try:
        str(path).encode("utf-8")
    except UnicodeError as exc:
        raise PipelineError("Invalid UTF-8 path") from exc
    path = Path(path)
    if ".." in path.parts:
        raise PipelineError("Parent traversal is not allowed")
    path = path.absolute()
    for component in (*reversed(path.parents), path):
        if component.is_symlink():
            raise PipelineError("Symlink paths are not allowed: " + str(component))
    return path


def _bounded_json(value):
    # Iterative inspection also catches cycles and invalid Unicode before dumps.
    stack = [(value, 0, False)]
    active = set()
    nodes = string_bytes = 0
    while stack:
        item, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(item))
            continue
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise PipelineError("JSON structure exceeds limits")
        if isinstance(item, str):
            if len(item) + string_bytes > MAX_JSON_BYTES:
                raise PipelineError("JSON exceeds size limit")
            try:
                string_bytes += len(item.encode("utf-8"))
            except UnicodeError as exc:
                raise PipelineError("Invalid UTF-8 input") from exc
            if string_bytes > MAX_JSON_BYTES:
                raise PipelineError("JSON exceeds size limit")
        elif isinstance(item, (dict, list)):
            if len(item) + nodes + len(stack) > MAX_NODES:
                raise PipelineError("JSON structure exceeds limits")
            if id(item) in active:
                raise PipelineError("Cyclic JSON structure")
            active.add(id(item))
            stack.append((item, depth, True))
            children = list(item.keys()) + list(item.values()) if isinstance(item, dict) else item
            if len(children) + nodes + len(stack) > MAX_NODES:
                raise PipelineError("JSON structure exceeds limits")
            stack.extend((child, depth + 1, False) for child in reversed(children))
        elif item is not None and type(item) not in (bool, int, float):
            raise PipelineError("Unsupported JSON value")
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        if len(raw.encode("utf-8")) > MAX_JSON_BYTES:
            raise PipelineError("JSON exceeds size limit")
        return raw
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise PipelineError("Invalid or oversized JSON input") from exc


def validate_campaign(data):
    _bounded_json(data)
    if not isinstance(data, dict) or set(data) != {"name", "assets"}:
        raise PipelineError("Campaign requires exactly name and assets")
    if not isinstance(data["name"], str) or not data["name"].strip():
        raise PipelineError("Campaign name must be nonempty")
    if not isinstance(data["assets"], list) or not data["assets"]:
        raise PipelineError("Campaign assets must be a nonempty list")
    if len(data["assets"]) > MAX_ASSETS:
        raise PipelineError("Too many assets")
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
    raw = _bounded_json(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _serialize_json(data):
    """Validate and serialize exactly as persisted, including formatting overhead."""
    _bounded_json(data)
    raw = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if len(raw.encode("utf-8")) > MAX_JSON_BYTES:
        raise PipelineError("JSON exceeds size limit")
    return raw


def _atomic_json(path, data):
    path = _safe_path(path)
    raw = _serialize_json(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read(path):
    try:
        with _safe_path(path).open("rb") as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise PipelineError("JSON exceeds size limit")
        data = json.loads(raw.decode("utf-8"))
        _bounded_json(data)
        return data
    except (OSError, ValueError, RecursionError) as exc:
        raise PipelineError("Cannot read JSON: " + str(path)) from exc


@contextmanager
def _locked(out):
    _safe_path(out)
    for name in (".pipeline.lock", "plan.json", "manifest.json", "assets"):
        _safe_path(out / name)
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


def _mock_record(asset, dependencies):
    return {"record_type": "mock_asset_record", "mock": True, "media_generated": False,
            "provider": "mock", "asset_id": asset["id"], "kind": asset["kind"],
            "prompt": asset["prompt"], "duration": asset.get("duration"),
            "approved_dependencies": list(dependencies),
            "notice": "MOCK ONLY: no media generated and no API called"}


class MockProvider:
    """Produces JSON metadata only, never images, videos, or invented media URLs."""
    def submit(self, asset, dependencies):
        return _mock_record(asset, dependencies)


def _validate_manifest(manifest, campaign, ordered):
    _bounded_json(manifest)
    if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
            or manifest.get("schema_version") != 2
            or manifest.get("fingerprint") != _fingerprint(campaign) or manifest.get("provider") != "mock"
            or _fingerprint(manifest.get("campaign")) != _fingerprint(campaign)
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
                    or set(approval) != {"reviewer", "at", "record_sha256"}
                    or approval.get("record_sha256") != state.get("record_sha256")
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
        expected_fields = {"status", "fingerprint", "approval"}
        if state["status"] == "completed":
            expected_fields |= {"record", "record_sha256"}
            expected_hash = _fingerprint(_mock_record(asset, asset["depends_on"]))
            if (state.get("record") != "assets/" + asset["id"] + ".json"
                    or state.get("record_sha256") != expected_hash):
                raise PipelineError("Invalid completed record state")
            if any(manifest["assets"][d].get("status") != "completed"
                   or not manifest["assets"][d].get("approval") for d in asset["depends_on"]):
                raise PipelineError("Completed asset requires completed approved dependencies")
        if set(state) != expected_fields:
            raise PipelineError("Unexpected asset state fields")
        if state["status"] == "submitting":
            raise PipelineError("Uncertain prior submission; manual reconciliation required, never automatically retried")


def _validate_records(out, manifest, ordered):
    for asset in ordered:
        aid = asset["id"]
        path = _safe_path(out / "assets" / (aid + ".json"))
        state = manifest["assets"][aid]
        if state["status"] == "completed":
            record = _read(path)
            # Hash canonical JSON, not formatting; exact schema, values and types.
            if (_bounded_json(record) != _bounded_json(_mock_record(asset, asset["depends_on"]))
                    or _fingerprint(record) != state["record_sha256"]):
                raise PipelineError("Completed record content mismatch: " + aid)


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
            manifest = {"schema_version": 2, "fingerprint": fingerprint, "provider": provider,
                        "campaign": campaign, "assets": {
                            a["id"]: {"status": "pending", "fingerprint": _fingerprint(a), "approval": None}
                            for a in ordered}}
        _validate_manifest(manifest, campaign, ordered)
        _validate_records(out, manifest, ordered)
        _atomic_json(path, manifest)
        for asset in ordered:
            state = manifest["assets"][asset["id"]]
            if state["status"] == "completed":
                continue
            deps = asset["depends_on"]
            if any(manifest["assets"][d]["status"] != "completed" or not manifest["assets"][d].get("approval") for d in deps):
                continue
            # Deterministic size/structure failures must not look like uncertain submissions.
            expected_record = _mock_record(asset, deps)
            completed_state = dict(state, status="completed",
                                   record="assets/" + asset["id"] + ".json",
                                   record_sha256=_fingerprint(expected_record))
            projected_assets = dict(manifest["assets"])
            projected_assets[asset["id"]] = completed_state
            _serialize_json(expected_record)
            _serialize_json(dict(manifest, assets=projected_assets))
            state["status"] = "submitting"
            _atomic_json(path, manifest)
            try:
                record = MockProvider().submit(asset, deps)
                if _fingerprint(record) != _fingerprint(_mock_record(asset, deps)):
                    raise PipelineError("Unexpected mock provider record")
                _atomic_json(out / "assets" / (asset["id"] + ".json"), record)
            except Exception as exc:
                raise PipelineError("Submission interrupted; refusing automatic retry") from exc
            state["status"] = "completed"
            state["record"] = "assets/" + asset["id"] + ".json"
            state["record_sha256"] = _fingerprint(record)
            _atomic_json(path, manifest)
    return manifest


def approve(out, asset_id, reviewer):
    """Record a named human approval of an existing completed mock record."""
    _bounded_json(reviewer)
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
        _validate_records(out, manifest, ordered)
        state = manifest["assets"].get(asset_id)
        if not state or state["status"] != "completed":
            raise PipelineError("Only completed assets may be approved")
        if not (out / "assets" / (asset_id + ".json")).is_file():
            raise PipelineError("Cannot approve a missing record")
        if not state.get("approval"):
            state["approval"] = {"reviewer": reviewer.strip(), "at": datetime.now(timezone.utc).isoformat(),
                                 "record_sha256": state["record_sha256"]}
            _atomic_json(path, manifest)
    return manifest
