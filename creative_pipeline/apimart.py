"""Pure APIMart payload examples and response URL parsing. No HTTP adapter.

These builders do not establish live provider compatibility or fetch references.
URLs are data only: callers would need a separate, reviewed network policy.
"""
from urllib.parse import urlsplit


def _https(value):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return False
    try:
        parsed = urlsplit(value)
        # Access validates malformed and out-of-range ports lazily.
        parsed.port
        return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def _prompt(prompt):
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be nonempty")


def image_payload(prompt):
    """Build an image request body; no submission occurs."""
    _prompt(prompt)
    return {"model": "gpt-image-2-ext", "prompt": prompt, "n": 1,
            "size": "9:16", "resolution": "2k", "official_fallback": False}


def video_payload(prompt, reference_urls, duration):
    """Build a single-reference frame video body; no submission occurs."""
    _prompt(prompt)
    if not isinstance(reference_urls, list) or len(reference_urls) != 1 or not _https(reference_urls[0]):
        raise ValueError("Exactly one HTTPS reference URL is required")
    if type(duration) is not int or duration not in (4, 6, 8, 10):
        raise ValueError("duration must be 4, 6, 8, or 10")
    return {"model": "Omni-Flash-Ext", "prompt": prompt, "generation_type": "frame",
            "image_urls": list(reference_urls), "duration": duration,
            "aspect_ratio": "9:16", "resolution": "720p"}


def extract_https_urls(result):
    """Collect unique HTTPS values iteratively, preserving traversal order.

    Values only (including video_urls), no fetching or media claims. Reject
    cycles, depth >32, >100000 nodes or >2 MiB of string values with ValueError.
    Shared acyclic containers are allowed and count on each visit.
    """
    urls, seen, active = [], set(), set()
    stack = [(result, 0, False)]
    nodes = string_bytes = 0
    while stack:
        value, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(value))
            continue
        nodes += 1
        if nodes > 100000 or depth > 32:
            raise ValueError("Response structure exceeds limits")
        if isinstance(value, (dict, list)):
            if id(value) in active:
                raise ValueError("Cyclic response structure")
            if len(value) + nodes + len(stack) > 100000:
                raise ValueError("Response structure exceeds limits")
            active.add(id(value))
            stack.append((value, depth, True))
            children = list(value.values()) if isinstance(value, dict) else value
            stack.extend((child, depth + 1, False) for child in reversed(children))
        elif isinstance(value, str):
            if len(value) + string_bytes > 2 * 1024 * 1024:
                raise ValueError("Response strings exceed size limit")
            try:
                string_bytes += len(value.encode("utf-8"))
            except UnicodeError as exc:
                raise ValueError("Invalid UTF-8 response") from exc
            if string_bytes > 2 * 1024 * 1024:
                raise ValueError("Response strings exceed size limit")
            if _https(value) and value not in seen:
                seen.add(value)
                urls.append(value)
    return urls
