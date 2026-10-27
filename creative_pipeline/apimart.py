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
    """Collect unique HTTPS string values recursively from nested dicts/lists.

    Includes video_urls arrays. Does not inspect dict keys, fetch URLs, or
    claim that returned URLs are media; pass the provider result subtree.
    """
    urls = []
    seen = set()

    def visit(value):
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif _https(value) and value not in seen:
            seen.add(value)
            urls.append(value)
    visit(result)
    return urls
